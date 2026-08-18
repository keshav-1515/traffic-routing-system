"""
Traffic Map Backend
--------------------
Core responsibilities (ALL logic lives here in Python):
  1. Download / build a real road network for a given place using OSMnx.
  2. Convert it into a NetworkX graph (nodes = intersections, edges = road segments).
  3. Enrich every edge with attributes needed for congestion modelling
     (length, free-flow speed, estimated capacity, free-flow travel time,
     congestion_score, and a routable `weight`).
  4. Serve the graph as GeoJSON over a small Flask API.
  5. Serve a route between two nodes computed with Dijkstra's algorithm.
  6. Serve a single HTML page that renders the graph on a Leaflet map
     (the frontend does ZERO computation — it only draws what Python sends it).

IMPORTANT: this script ONLY draws a real road network if you give it a real
place or bounding box. Without one, it falls back to a synthetic grid with
made-up coordinates, which will NEVER line up with real map tiles.

Run:
    python virtual_world.py --place "Koramangala, Bangalore, India"
    python virtual_world.py --bbox 12.935 12.925 77.635 77.615   # north south east west
    python virtual_world.py                                     # synthetic grid (offline demo only)
"""

import argparse
import random
import sys

import networkx as nx
from flask import Flask, jsonify, render_template, request

import traffic_engine

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Global graph object — built once at startup, reused by every API call.
# This IS the actual routable data structure: a networkx.MultiDiGraph where
# every node is a real intersection and every edge is a real road segment.
# ---------------------------------------------------------------------------
ROAD_GRAPH = None
USING_SYNTHETIC = False

# The live traffic-flow AI engine (traffic_engine.py). It receives this
# SAME graph object and mutates its edge attributes in place every tick,
# so /api/graph and /api/route stay live with zero changes to those routes.
SIM: "traffic_engine.TrafficSimulation" = None


# ---------------------------------------------------------------------------
# 1. ROAD TYPE -> (lanes, free-flow speed km/h) heuristics
# ---------------------------------------------------------------------------
ROAD_PROFILES = {
    "motorway":      {"lanes": 4, "speed_kmh": 90},
    "trunk":         {"lanes": 3, "speed_kmh": 70},
    "primary":       {"lanes": 3, "speed_kmh": 55},
    "secondary":     {"lanes": 2, "speed_kmh": 45},
    "tertiary":      {"lanes": 2, "speed_kmh": 40},
    "residential":   {"lanes": 1, "speed_kmh": 30},
    "living_street": {"lanes": 1, "speed_kmh": 20},
    "unclassified":  {"lanes": 1, "speed_kmh": 30},
    "service":       {"lanes": 1, "speed_kmh": 20},
}
DEFAULT_PROFILE = {"lanes": 1, "speed_kmh": 30}

CAPACITY_PER_LANE_PER_HOUR = 1800  # standard traffic-engineering rule of thumb (veh/hr/lane)

# BPR (Bureau of Public Roads) congestion penalty constants — the standard
# formula traffic engineers use to turn a congestion ratio into a travel-time
# penalty: travel_time = free_flow_time * (1 + ALPHA * congestion**BETA)
BPR_ALPHA = 0.15
BPR_BETA = 4


def _road_profile(highway_tag):
    """OSM 'highway' tag can be a string or a list of strings — normalise it."""
    if isinstance(highway_tag, list):
        highway_tag = highway_tag[0]
    return ROAD_PROFILES.get(highway_tag, DEFAULT_PROFILE)


def enrich_edge(data):
    """
    Attach every attribute the routing/congestion engine needs, directly on
    the graph edge (not computed later in the API layer). This means the
    SAME graph object can be handed straight to nx.dijkstra_path().
    """
    profile = _road_profile(data.get("highway", "unclassified"))
    lanes = data.get("lanes")
    try:
        lanes = int(lanes) if lanes and not isinstance(lanes, list) else profile["lanes"]
    except (ValueError, TypeError):
        lanes = profile["lanes"]

    length_m = data.get("length", 50.0)
    speed_kmh = profile["speed_kmh"]

    data["lanes"] = lanes
    data["free_flow_speed_kmh"] = speed_kmh
    data["capacity_veh_per_hr"] = lanes * CAPACITY_PER_LANE_PER_HOUR
    data["free_flow_time_min"] = round((length_m / 1000) / speed_kmh * 60, 3)

    # current_volume will be populated later by a real traffic simulation
    # module. For now it seeds a congestion_score so the graph is routable
    # from the moment it's built.
    data["current_volume"] = 0
    if "congestion_score" not in data:
        data["congestion_score"] = round(random.uniform(0.05, 0.95), 2)

    recompute_weight(data)
    return data


def recompute_weight(data):
    """
    Turn (distance, congestion) into a single routable cost, stored as
    `weight` directly on the edge. This is what Dijkstra/A* should use as
    the `weight=` argument.

    weight_min = free_flow_time_min * (1 + ALPHA * congestion_score^BETA)

    A congestion_score of 0 gives you the free-flow time; a congestion_score
    approaching 1 (gridlock) multiplies travel time up sharply, exactly like
    the standard BPR congestion function used in real traffic models.
    """
    penalty = 1 + BPR_ALPHA * (data.get("congestion_score", 0.0) ** BPR_BETA)
    data["weight"] = round(data["free_flow_time_min"] * penalty, 4)
    return data["weight"]


def set_edge_congestion(G, u, v, k, congestion_score):
    """
    Update congestion on a single edge and keep its `weight` in sync.
    This is the hook a future live-traffic simulation module should call.
    """
    data = G[u][v][k]
    data["congestion_score"] = max(0.0, min(1.0, congestion_score))
    recompute_weight(data)


def randomize_congestion(G, seed=None):
    """
    Assign a congestion_score (and derived weight) to every edge ONCE.
    Unlike the old code, this runs at graph-build time and the values are
    stored on the graph itself — so congestion is stable across API calls
    and consistent between what you see on the map and what Dijkstra routes
    against. Call this again later from a simulation loop to "tick" traffic.
    """
    if seed is not None:
        random.seed(seed)
    for _, _, _, data in G.edges(keys=True, data=True):
        data["congestion_score"] = round(random.uniform(0.05, 0.95), 2)
        recompute_weight(data)


# ---------------------------------------------------------------------------
# 2. Build the graph — real data via OSMnx, or a synthetic grid fallback
# ---------------------------------------------------------------------------
def build_graph_from_place(place_name, dist=1500):
    import osmnx as ox

    print(f"[graph] Downloading road network for: {place_name}")
    try:
        # Works when Nominatim has a clean administrative POLYGON for the
        # place (cities, wards, official boundaries). Many neighbourhoods
        # (e.g. "Koramangala") only geocode to a POINT, so this raises.
        G = ox.graph_from_place(place_name, network_type="drive", simplify=True)
    except Exception as e:
        print(f"[graph] graph_from_place failed ({e}); "
              f"retrying as a point + {dist}m radius instead", file=sys.stderr)
        # Geocodes the place name to a single point via Nominatim, then
        # pulls every road within `dist` metres of it. This is the reliable
        # method for neighbourhoods/localities that lack a polygon boundary.
        G = ox.graph_from_address(place_name, dist=dist,
                                   network_type="drive", simplify=True)

    for _, _, _, data in G.edges(keys=True, data=True):
        enrich_edge(data)
    return G


def build_graph_from_bbox(north, south, east, west):
    import osmnx as ox
    print(f"[graph] Downloading road network for bbox N{north} S{south} E{east} W{west}")
    try:
        # OSMnx >= 2.0 API: single bbox tuple (west, south, east, north)
        G = ox.graph_from_bbox(bbox=(west, south, east, north),
                                network_type="drive", simplify=True)
    except TypeError:
        # OSMnx < 2.0 API: separate north/south/east/west kwargs
        G = ox.graph_from_bbox(north, south, east, west,
                                network_type="drive", simplify=True)
    for _, _, _, data in G.edges(keys=True, data=True):
        enrich_edge(data)
    return G


def build_synthetic_grid(rows=8, cols=8, spacing_m=120, origin_lat=12.9716, origin_lng=77.5946):
    """
    Fallback road network: an R x C grid of intersections, used ONLY when
    there's no place/bbox given (offline dev / demo). Coordinates are made
    up around origin_lat/lng, so this will NOT align with real roads on the
    map — that is expected, not a bug. Pass --place or --bbox for real data.
    """
    print(f"[graph] Building synthetic {rows}x{cols} grid (no internet / no place given)")
    G = nx.MultiDiGraph()
    meters_per_deg_lat = 111_320
    meters_per_deg_lng = 111_320

    node_id = lambda r, c: r * cols + c

    for r in range(rows):
        for c in range(cols):
            lat = origin_lat + (r * spacing_m) / meters_per_deg_lat
            lng = origin_lng + (c * spacing_m) / meters_per_deg_lng
            G.add_node(node_id(r, c), x=lng, y=lat)

    highway_choices = ["primary", "secondary", "tertiary", "residential"]

    def add_road(a, b, length_m):
        highway = random.choice(highway_choices)
        data = {"highway": highway, "length": length_m, "oneway": False}
        enrich_edge(data)
        G.add_edge(a, b, **data)
        data_rev = dict(data)
        G.add_edge(b, a, **data_rev)

    for r in range(rows):
        for c in range(cols):
            if c < cols - 1:
                add_road(node_id(r, c), node_id(r, c + 1), spacing_m)
            if r < rows - 1:
                add_road(node_id(r, c), node_id(r + 1, c), spacing_m)

    G.graph["crs"] = "epsg:4326"
    return G


def init_graph(place=None, bbox=None, dist=1500, signal_mode="adaptive",
                tick_seconds=2.0, sim_minutes_per_tick=1.0, autostart=True):
    global ROAD_GRAPH, USING_SYNTHETIC, SIM
    try:
        if place:
            ROAD_GRAPH = build_graph_from_place(place, dist=dist)
        elif bbox:
            north, south, east, west = bbox
            ROAD_GRAPH = build_graph_from_bbox(north, south, east, west)
        else:
            raise RuntimeError("No place/bbox given, using synthetic grid.")
        USING_SYNTHETIC = False
    except Exception as e:
        print(f"[graph] Falling back to synthetic grid. Reason: {e}", file=sys.stderr)
        ROAD_GRAPH = build_synthetic_grid()
        USING_SYNTHETIC = True
        # synthetic grid already seeds congestion per-edge in enrich_edge,
        # but real-graph edges need it too:
    if not USING_SYNTHETIC:
        randomize_congestion(ROAD_GRAPH)

    n_nodes = ROAD_GRAPH.number_of_nodes()
    n_edges = ROAD_GRAPH.number_of_edges()
    print(f"[graph] Ready: {n_nodes} nodes (intersections), {n_edges} directed edges "
          f"({'synthetic' if USING_SYNTHETIC else 'real OSM data'})")

    # Hand the graph to the live simulation/AI engine. It mutates the same
    # edge dicts in place (congestion_score, weight, current_speed_kmh, ...)
    # so every existing route below keeps working unmodified.
    SIM = traffic_engine.TrafficSimulation(
        ROAD_GRAPH,
        recompute_weight_fn=recompute_weight,
        tick_seconds=tick_seconds,
        sim_minutes_per_tick=sim_minutes_per_tick,
        signal_mode=signal_mode,
    )
    if autostart:
        SIM.start()
        print(f"[sim] Live traffic simulation started "
              f"(tick every {tick_seconds}s = {sim_minutes_per_tick} sim-min, signals: {signal_mode})")


# ---------------------------------------------------------------------------
# 3. Routing — Dijkstra / shortest path on the SAME graph object
# ---------------------------------------------------------------------------
def shortest_path(G, source, target, by="time"):
    """
    Run Dijkstra's algorithm on the routable graph.

    by="time"     -> uses the congestion-adjusted `weight` attribute
                     (realistic ETA-style routing)
    by="distance" -> uses raw `length` in metres (shortest physical route)

    Returns dict with the node path, total distance (m), total time (min),
    and a GeoJSON LineString for drawing the route on the map.
    """
    weight_key = "weight" if by == "time" else "length"
    path = nx.dijkstra_path(G, source, target, weight=weight_key)

    total_length_m = 0.0
    total_time_min = 0.0
    coords = []

    for u, v in zip(path[:-1], path[1:]):
        # pick the cheapest parallel edge between u and v (MultiDiGraph)
        edge_data = min(G[u][v].values(), key=lambda d: d[weight_key])
        total_length_m += edge_data.get("length", 0.0)
        total_time_min += edge_data.get("weight", 0.0)
        if not coords:
            coords.append([G.nodes[u]["x"], G.nodes[u]["y"]])
        coords.append([G.nodes[v]["x"], G.nodes[v]["y"]])

    return {
        "path": [str(n) for n in path],
        "distance_m": round(total_length_m, 1),
        "time_min": round(total_time_min, 2),
        "geometry": {"type": "LineString", "coordinates": coords},
    }


# ---------------------------------------------------------------------------
# 4. Convert the NetworkX graph into GeoJSON the frontend can render directly
# ---------------------------------------------------------------------------
def graph_to_geojson(G):
    node_features = []
    for node_id, data in G.nodes(data=True):
        node_features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [data["x"], data["y"]]},
            "properties": {"id": str(node_id)},
        })

    edge_features = []
    seen = set()  # dedupe A->B / B->A pairs so the map isn't drawn twice
    for u, v, k, data in G.edges(keys=True, data=True):
        pair_key = tuple(sorted((u, v)))
        if pair_key in seen:
            continue
        seen.add(pair_key)

        if "geometry" in data and hasattr(data["geometry"], "coords"):
            coords = [[x, y] for x, y in data["geometry"].coords]
        else:
            u_data, v_data = G.nodes[u], G.nodes[v]
            coords = [[u_data["x"], u_data["y"]], [v_data["x"], v_data["y"]]]

        edge_features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "u": str(u),
                "v": str(v),
                "k": str(k),
                "highway": str(data.get("highway", "unclassified")),
                "lanes": data.get("lanes"),
                "length_m": round(data.get("length", 0), 1),
                "free_flow_speed_kmh": data.get("free_flow_speed_kmh"),
                "capacity_veh_per_hr": data.get("capacity_veh_per_hr"),
                "free_flow_time_min": data.get("free_flow_time_min"),
                "weight": data.get("weight"),
                # stored on the graph now, kept live by traffic_engine.TrafficSimulation
                "congestion_score": data.get("congestion_score"),
                "current_speed_kmh": data.get("current_speed_kmh"),
                "current_volume": data.get("current_volume"),
                "queue_estimate": data.get("queue_estimate"),
                "signal_delay_min": data.get("signal_delay_min"),
                "incident": data.get("incident", False),
            },
        })

    return {
        "nodes": {"type": "FeatureCollection", "features": node_features},
        "edges": {"type": "FeatureCollection", "features": edge_features},
    }


# ---------------------------------------------------------------------------
# 5. Flask routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    center_lat, center_lng = _graph_center()
    return render_template("index.html", center_lat=center_lat, center_lng=center_lng)


@app.route("/api/graph")
def api_graph():
    geojson = graph_to_geojson(ROAD_GRAPH)
    return jsonify({
        "using_synthetic": USING_SYNTHETIC,
        "node_count": ROAD_GRAPH.number_of_nodes(),
        "edge_count": len(geojson["edges"]["features"]),
        **geojson,
    })


@app.route("/api/route")
def api_route():
    """
    Example: /api/route?source=12&target=48&by=time
    `source`/`target` are node ids as returned in /api/graph node properties.
    """
    source = request.args.get("source", type=int)
    target = request.args.get("target", type=int)
    by = request.args.get("by", default="time")
    if source is None or target is None:
        return jsonify({"error": "source and target query params are required"}), 400
    try:
        result = shortest_path(ROAD_GRAPH, source, target, by=by)
    except nx.NetworkXNoPath:
        return jsonify({"error": f"no path between {source} and {target}"}), 404
    except nx.NodeNotFound as e:
        return jsonify({"error": str(e)}), 404
    return jsonify(result)


def _graph_center():
    lats = [d["y"] for _, d in ROAD_GRAPH.nodes(data=True)]
    lngs = [d["x"] for _, d in ROAD_GRAPH.nodes(data=True)]
    return sum(lats) / len(lats), sum(lngs) / len(lngs)


# ---------------------------------------------------------------------------
# 5b. Simulation / AI-engine routes — all logic lives in traffic_engine.py,
#     these routes just expose it.
# ---------------------------------------------------------------------------
def _find_edge(u, v, k=None):
    u = int(u) if str(u).lstrip("-").isdigit() else u
    v = int(v) if str(v).lstrip("-").isdigit() else v
    if v not in ROAD_GRAPH[u]:
        return None
    if k is not None and k in ROAD_GRAPH[u][v]:
        return (u, v, k)
    # fall back to the first/only parallel edge
    return (u, v, next(iter(ROAD_GRAPH[u][v])))


@app.route("/api/simulate/state")
def api_simulate_state():
    """Live snapshot: sim clock, active vehicles, incidents, signals, CO2,
    and per-edge congestion/speed/queue — everything the dashboard polls."""
    return jsonify(SIM.get_state())


@app.route("/api/simulate/start", methods=["POST"])
def api_simulate_start():
    SIM.start()
    return jsonify({"running": SIM.running})


@app.route("/api/simulate/stop", methods=["POST"])
def api_simulate_stop():
    SIM.stop()
    return jsonify({"running": SIM.running})


@app.route("/api/simulate/reroute_preview")
def api_simulate_reroute_preview():
    """Example: /api/simulate/reroute_preview?u=4&v=5&k=0
    After a block/jam/boost action, this reports the current cheapest
    path between the edge's endpoints so the frontend can highlight the
    detour real traffic would now take."""
    edge = _find_edge(request.args.get("u"), request.args.get("v"), request.args.get("k"))
    if edge is None:
        return jsonify({"error": "no such edge"}), 404
    preview = SIM.get_reroute_preview(edge)
    if preview is None:
        return jsonify({"error": "no path found"}), 404
    return jsonify(preview)


@app.route("/api/simulate/jam", methods=["POST"])
def api_simulate_jam():
    """Body: {"u":.., "v":.., "k":.., "duration_min": 15, "capacity_factor": 0.35}
    The 'Traffic jam' action — degrades a road's usable capacity for a
    limited time (a stalled vehicle, a pothole, an accident, ...)."""
    body = request.get_json(silent=True) or {}
    edge = _find_edge(body.get("u"), body.get("v"), body.get("k"))
    if edge is None:
        return jsonify({"error": "no such edge"}), 404
    ov = SIM.set_capacity_factor(
        edge, factor=float(body.get("capacity_factor", 0.35)),
        duration_min=float(body.get("duration_min", 15.0)), label=body.get("label", "traffic jam"),
    )
    return jsonify({"edge": [str(x) for x in edge], "capacity_factor": ov.capacity_factor,
                    "clears_in_min": ov.expires_at_min})


@app.route("/api/simulate/boost", methods=["POST"])
def api_simulate_boost():
    """Body: {"u":.., "v":.., "k":.., "duration_min": 15, "capacity_factor": 1.6}
    The 'Increase flow' action — temporarily boosts a road's usable
    capacity (extra lane / tidal-lane reversal / hard signal priority)."""
    body = request.get_json(silent=True) or {}
    edge = _find_edge(body.get("u"), body.get("v"), body.get("k"))
    if edge is None:
        return jsonify({"error": "no such edge"}), 404
    ov = SIM.set_capacity_factor(
        edge, factor=float(body.get("capacity_factor", 1.6)),
        duration_min=float(body.get("duration_min", 15.0)), label=body.get("label", "flow boost"),
    )
    return jsonify({"edge": [str(x) for x in edge], "capacity_factor": ov.capacity_factor,
                    "clears_in_min": ov.expires_at_min})


@app.route("/api/simulate/reset", methods=["POST"])
def api_simulate_reset():
    """Body: {"u":.., "v":.., "k":..} — clears every manual override on a segment."""
    body = request.get_json(silent=True) or {}
    edge = _find_edge(body.get("u"), body.get("v"), body.get("k"))
    if edge is None:
        return jsonify({"error": "no such edge"}), 404
    SIM.reset_edge(edge)
    return jsonify({"edge": [str(x) for x in edge], "reset": True})


@app.route("/api/simulate/zone", methods=["POST"])
def api_simulate_zone():
    """Body: {"u":.., "v":.., "k":.., "zone_type": "hospital"|"school"|"emergency"|null}"""
    body = request.get_json(silent=True) or {}
    edge = _find_edge(body.get("u"), body.get("v"), body.get("k"))
    if edge is None:
        return jsonify({"error": "no such edge"}), 404
    zone_type = body.get("zone_type")
    if zone_type not in (None, "hospital", "school", "emergency"):
        return jsonify({"error": "zone_type must be hospital, school, emergency, or null"}), 400
    SIM.set_zone(edge, zone_type)
    return jsonify({"edge": [str(x) for x in edge], "zone_type": zone_type})


@app.route("/api/simulate/peak_surge", methods=["POST"])
def api_simulate_peak_surge():
    """Body: {"u":.., "v":.., "k":.., "duration_min": 30, "intensity": 2.6}
    The 'Peak-hour surge' action — automatically runs a self-contained
    rush-hour simulation; u/v/k are optional (omit for a network-wide
    surge instead of one concentrated on a corridor)."""
    body = request.get_json(silent=True) or {}
    edge = None
    if "u" in body and "v" in body:
        edge = _find_edge(body["u"], body["v"], body.get("k"))
    SIM.simulate_peak_surge(
        edge=edge, duration_min=float(body.get("duration_min", 30.0)),
        intensity=float(body.get("intensity", 2.6)),
    )
    return jsonify({"edge": [str(x) for x in edge] if edge else None,
                    "duration_min": body.get("duration_min", 30.0)})


@app.route("/api/simulate/vehicles")
def api_simulate_vehicles():
    """Live vehicle positions (interpolated lat/lng) for the moving-dots
    traffic-flow visualisation. ?limit=400 caps how many are returned."""
    limit = request.args.get("limit", default=400, type=int)
    return jsonify({"vehicles": SIM.get_vehicle_positions(limit=limit)})


@app.route("/api/simulate/node")
def api_simulate_node():
    """Example: /api/simulate/node?id=48 — aggregated stats + signal state
    for an intersection, shown when a node is clicked on the map."""
    node_id = request.args.get("id")
    if node_id is None:
        return jsonify({"error": "id query param required"}), 400
    node = int(node_id) if node_id.lstrip("-").isdigit() else node_id
    info = SIM.get_node_info(node)
    if info is None:
        return jsonify({"error": "no such node"}), 404
    return jsonify(info)


@app.route("/api/simulate/demand", methods=["POST"])
def api_simulate_demand():
    """Body: {"scale": 0.0-4.0} — live trip-generation intensity multiplier,
    driven by the dashboard's 'Traffic density' slider."""
    body = request.get_json(silent=True) or {}
    scale = float(body.get("scale", 1.0))
    SIM.set_demand_scale(scale)
    return jsonify({"demand_scale": SIM.demand_scale})


@app.route("/api/simulate/closure", methods=["POST"])
def api_simulate_closure():
    """Body: {"u":.., "v":.., "k":.., "closed": true|false}"""
    body = request.get_json(silent=True) or {}
    edge = _find_edge(body.get("u"), body.get("v"), body.get("k"))
    if edge is None:
        return jsonify({"error": "no such edge"}), 404
    SIM.set_road_closed(edge, bool(body.get("closed", True)))
    return jsonify({"edge": [str(x) for x in edge], "closed": bool(body.get("closed", True))})


@app.route("/api/simulate/emergency", methods=["POST"])
def api_simulate_emergency():
    """Body: {"path": [node_id, node_id, ...], "speed_kmh": 60}
    Grants an uninterrupted green wave along the given node path (e.g. a
    route already computed via /api/route) for an ambulance/fire/transit
    vehicle, and spawns a tracked probe vehicle along it."""
    body = request.get_json(silent=True) or {}
    path = body.get("path", [])
    path = [int(n) if str(n).lstrip("-").isdigit() else n for n in path]
    if len(path) < 2:
        return jsonify({"error": "path must have >= 2 nodes"}), 400
    veh = SIM.emergency_green_wave(path, vehicle_speed_kmh=float(body.get("speed_kmh", 60.0)))
    return jsonify({"scheduled_nodes": len(path), "vehicle_id": veh.vid if veh else None})


@app.route("/api/forecast")
def api_forecast():
    """Example: /api/forecast?u=12&v=48&k=0&horizons=15,30,60"""
    edge = _find_edge(request.args.get("u"), request.args.get("v"), request.args.get("k"))
    if edge is None:
        return jsonify({"error": "no such edge"}), 404
    horizons = [int(h) for h in request.args.get("horizons", "15,30,60").split(",")]
    return jsonify({"edge": [str(x) for x in edge], "forecast": SIM.forecast(edge, horizons)})


@app.route("/api/whatif", methods=["POST"])
def api_whatif():
    """
    Counterfactual "before vs after" analysis via Frank-Wolfe UE traffic
    assignment (see traffic_engine.compare_scenarios). Body:
        {"closures": [[u,v,k], [u,v,k]], "capacity_multipliers": {"u,v,k": 0.5}}
    Both fields optional; an empty body still returns a baseline snapshot.
    """
    body = request.get_json(silent=True) or {}
    closures = []
    for triple in body.get("closures", []):
        e = _find_edge(*triple) if len(triple) == 3 else _find_edge(triple[0], triple[1])
        if e:
            closures.append(e)
    cap_mult = {}
    for key, mult in (body.get("capacity_multipliers") or {}).items():
        parts = key.split(",")
        e = _find_edge(*parts) if len(parts) == 3 else _find_edge(parts[0], parts[1])
        if e:
            cap_mult[e] = float(mult)
    result = traffic_engine.compare_scenarios(
        ROAD_GRAPH, close_edges=closures, capacity_multipliers=cap_mult
    )
    return jsonify(result)


# ---------------------------------------------------------------------------
# 6. CLI entrypoint
# ---------------------------------------------------------------------------
def parse_args():
    parser = argparse.ArgumentParser(description="City road-network graph viewer")
    parser.add_argument("--place", type=str, default=None,
                         help='e.g. --place "Koramangala, Bangalore, India"')
    parser.add_argument("--bbox", type=float, nargs=4, default=None,
                         metavar=("NORTH", "SOUTH", "EAST", "WEST"),
                         help="e.g. --bbox 12.935 12.925 77.635 77.615")
    parser.add_argument("--dist", type=int, default=1500,
                         help="Radius in metres to pull around --place when it has no "
                              "Nominatim polygon boundary (default: 1500)")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--signal-mode", choices=["fixed", "adaptive", "qlearning"],
                         default="adaptive", help="Traffic-signal control strategy (default: adaptive)")
    parser.add_argument("--tick-seconds", type=float, default=2.0,
                         help="Real seconds between simulation ticks (default: 2.0)")
    parser.add_argument("--sim-minutes-per-tick", type=float, default=1.0,
                         help="Simulated minutes advanced per tick (default: 1.0)")
    parser.add_argument("--no-autostart", action="store_true",
                         help="Build the graph but don't start the live simulation loop")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    init_graph(place=args.place, bbox=args.bbox, dist=args.dist,
               signal_mode=args.signal_mode, tick_seconds=args.tick_seconds,
               sim_minutes_per_tick=args.sim_minutes_per_tick,
               autostart=not args.no_autostart)
    app.run(host="0.0.0.0", port=args.port, debug=True, use_reloader=False)