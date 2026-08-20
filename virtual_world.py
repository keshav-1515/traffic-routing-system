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
import tempfile
import uuid

import networkx as nx
from flask import Flask, jsonify, render_template, request

import traffic_engine
import time
# CV engine integration
try:
    from cv_engine import DEFAULT_CV
except Exception:
    DEFAULT_CV = None

try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Standalone "demo vehicle" simulation — a lightweight, admin-controlled,
# spawn-and-step vehicle tracker that is INTENTIONALLY separate from the
# always-on background TrafficSimulation (SIM) above. SIM continuously
# generates city-wide traffic on its own clock via a Poisson demand process;
# this one lets an operator spawn a small tracked batch of vehicles on
# demand and step them forward one click/tick at a time — handy for a live
# walkthrough demo ("watch these 15 cars find their route, now let's block
# a road and watch them react") without touching the live simulation state.
# It still routes against the SAME graph (ROAD_GRAPH) and therefore the SAME
# live `weight` values SIM/the admin API keeps up to date, so its vehicles
# react to every closure/jam/boost/zone/peak-hour change exactly like real
# traffic would — it just isn't itself driving congestion_score/current_volume.
# ---------------------------------------------------------------------------
DEMO_VEHICLES = {}
DEMO_SIM_TICK_COUNT = 0

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
    base = data["free_flow_time_min"] * penalty
    # incident penalty: if an incident is present, scale travel time up
    incident_info = data.get("incident_info")
    if incident_info:
        try:
            sev = float(incident_info.get("severity", 0.7))
        except Exception:
            sev = 0.7
        INCIDENT_WEIGHT_ALPHA = 3.0
        base = base * (1.0 + INCIDENT_WEIGHT_ALPHA * sev)
    data["weight"] = round(base, 4)
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
# 1b. Demo vehicle simulation (spawn / tick / positions) — see the module
#     docstring note above DEMO_VEHICLES for why this is separate from SIM.
# ---------------------------------------------------------------------------
def _random_routable_pair():
    """Pick a random (source, target) node pair that actually has a path."""
    nodes = list(ROAD_GRAPH.nodes)
    for _ in range(50):
        a, b = random.sample(nodes, 2)
        if nx.has_path(ROAD_GRAPH, a, b):
            return a, b
    raise RuntimeError("Could not find a routable node pair in the graph")


def demo_spawn_vehicles(count):
    spawned = []
    for _ in range(count):
        try:
            source, target = _random_routable_pair()
            path = nx.dijkstra_path(ROAD_GRAPH, source, target, weight="weight")
        except (RuntimeError, nx.NetworkXNoPath):
            continue
        vid = uuid.uuid4().hex[:8]
        DEMO_VEHICLES[vid] = {
            "id": vid, "source": source, "target": target, "path": path,
            "edge_index": 0, "progress": 0.0, "status": "active",
            "color": random.choice(["#3388ff", "#8e44ad", "#16a085", "#e67e22", "#2c3e50"]),
        }
        spawned.append(vid)
    return spawned


def _demo_reroute_vehicle(vehicle):
    """Recompute the remaining route from the vehicle's current node using
    the latest live weights, so demo vehicles react to closures/jams/zone
    changes just like SIM's own vehicles do."""
    path = vehicle["path"]
    current_node = path[vehicle["edge_index"]]
    try:
        new_path = nx.dijkstra_path(ROAD_GRAPH, current_node, vehicle["target"], weight="weight")
    except nx.NetworkXNoPath:
        return
    vehicle["path"] = new_path
    vehicle["edge_index"] = 0


def demo_tick_simulation(dt_min=0.5, reroute_every=4):
    """Advance every active demo vehicle by dt_min simulated minutes along
    its route, periodically re-routing against live weights."""
    global DEMO_SIM_TICK_COUNT
    DEMO_SIM_TICK_COUNT += 1

    active_vehicles = [v for v in DEMO_VEHICLES.values() if v["status"] == "active"]
    if DEMO_SIM_TICK_COUNT % reroute_every == 0:
        for vehicle in active_vehicles:
            _demo_reroute_vehicle(vehicle)

    for vehicle in active_vehicles:
        path = vehicle["path"]
        if vehicle["edge_index"] >= len(path) - 1:
            vehicle["status"] = "arrived"
            continue
        u, v = path[vehicle["edge_index"]], path[vehicle["edge_index"] + 1]
        if not ROAD_GRAPH.has_edge(u, v):
            vehicle["status"] = "arrived"  # graph changed under it; stop gracefully
            continue
        edge_data = min(ROAD_GRAPH[u][v].values(), key=lambda d: d.get("weight", 0.0))
        edge_time_min = edge_data.get("free_flow_time_min", 0.1) or 0.1
        vehicle["progress"] += dt_min / max(edge_time_min, 0.05)
        if vehicle["progress"] >= 1.0:
            vehicle["progress"] = 0.0
            vehicle["edge_index"] += 1
            if vehicle["edge_index"] >= len(path) - 1:
                vehicle["status"] = "arrived"

    return {
        "tick": DEMO_SIM_TICK_COUNT,
        "active": len(active_vehicles),
        "arrived": sum(1 for v in DEMO_VEHICLES.values() if v["status"] == "arrived"),
    }


def demo_vehicle_positions():
    """Interpolate every demo vehicle's current lat/lng along its current
    edge, for the frontend's 'Demo Vehicles' tab markers."""
    positions = []
    for vehicle in DEMO_VEHICLES.values():
        path = vehicle["path"]
        if len(path) < 2:
            continue
        idx = min(vehicle["edge_index"], len(path) - 2)
        u, v = path[idx], path[idx + 1]
        if u not in ROAD_GRAPH.nodes or v not in ROAD_GRAPH.nodes:
            continue
        u_data, v_data = ROAD_GRAPH.nodes[u], ROAD_GRAPH.nodes[v]
        t = vehicle["progress"] if vehicle["status"] == "active" else 1.0
        lng = u_data["x"] + (v_data["x"] - u_data["x"]) * t
        lat = u_data["y"] + (v_data["y"] - u_data["y"]) * t
        positions.append({
            "id": vehicle["id"], "lat": lat, "lng": lng,
            "status": vehicle["status"], "color": vehicle["color"],
        })
    return positions


# ---------------------------------------------------------------------------
# 1c. Computer-vision traffic analysis (traffic-camera IoT input type)
# ---------------------------------------------------------------------------
def analyze_traffic_video(file_storage):
    """
    Estimate a congestion_score from an uploaded traffic-camera clip using
    classic OpenCV background subtraction + contour counting — a real,
    dependency-light motion-density heuristic (moving blobs above a size
    threshold are treated as vehicles). This is intentionally simpler than a
    full YOLOv8 + tracker pipeline (no model weights needed, runs anywhere
    OpenCV runs); swapping in a proper detector/tracker later is a drop-in
    replacement for this function's internals — everything downstream (the
    edge update via SIM.set_manual_congestion) stays the same.
    """
    if not CV2_AVAILABLE:
        raise RuntimeError(
            "opencv-python-headless is not installed on the server. "
            "Run: pip install opencv-python-headless numpy"
        )

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        file_storage.save(tmp.name)
        tmp_path = tmp.name

    cap = cv2.VideoCapture(tmp_path)
    if not cap.isOpened():
        raise RuntimeError("Could not open uploaded video — is it a valid video file?")

    bg_subtractor = cv2.createBackgroundSubtractorMOG2(history=200, varThreshold=32, detectShadows=False)
    min_blob_area = 350  # pixels; filters out noise / small artifacts

    frame_count = 0
    moving_object_counts = []
    max_frames = 300  # cap analysis time on long uploads

    while frame_count < max_frames:
        ok, frame = cap.read()
        if not ok:
            break
        frame_count += 1

        mask = bg_subtractor.apply(frame)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        mask = cv2.dilate(mask, np.ones((5, 5), np.uint8), iterations=1)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        vehicle_like = sum(1 for c in contours if cv2.contourArea(c) >= min_blob_area)

        # Skip the first ~20 frames while the background model is still
        # learning the empty road (those counts are unreliable).
        if frame_count > 20:
            moving_object_counts.append(vehicle_like)

    cap.release()

    if not moving_object_counts:
        raise RuntimeError("Video too short to analyze (need at least ~1 second of footage)")

    avg_moving_objects = sum(moving_object_counts) / len(moving_object_counts)
    peak_moving_objects = max(moving_object_counts)

    # Heuristic density -> congestion_score mapping: calibrated so ~10
    # simultaneous moving vehicles in frame reads as "gridlock" (1.0).
    density_reference = 10.0
    congestion_score = round(min(1.0, avg_moving_objects / density_reference), 2)

    return {
        "frames_analyzed": frame_count,
        "avg_moving_objects": round(avg_moving_objects, 2),
        "peak_moving_objects": peak_moving_objects,
        "estimated_congestion_score": congestion_score,
        "method": "OpenCV MOG2 background subtraction + contour blob counting",
    }


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
    # start CV manager in mock mode by default so /api/cv/metrics is available
    try:
        if DEFAULT_CV is not None:
            DEFAULT_CV.mode = 'mock'
            DEFAULT_CV.start()
            print('[cv] DEFAULT_CV started (mock)')
    except Exception:
        pass
    # background thread: push CV metrics into SIM for incident detection
    try:
        import threading
        def _push_cv_loop():
            while True:
                try:
                    if DEFAULT_CV is not None and SIM is not None:
                        m = DEFAULT_CV.get_metrics()
                        if hasattr(SIM, 'set_external_cv_metrics'):
                            SIM.set_external_cv_metrics(m)
                except Exception:
                    pass
                time.sleep(1.0)
        t = threading.Thread(target=_push_cv_loop, daemon=True)
        t.start()
    except Exception:
        pass


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
        # current (live) best route under current weights
        current = shortest_path(ROAD_GRAPH, source, target, by=by)
    except nx.NetworkXNoPath:
        return jsonify({"error": f"no path between {source} and {target}"}), 404
    except nx.NodeNotFound as e:
        return jsonify({"error": str(e)}), 404

    # Build baseline graph copy with incident-free weights (if SIM present)
    baseline_eta = None
    recommended = None
    time_saved_min = 0.0
    co2_saved_g = 0.0
    rerouted = False
    rerouting_reason = None
    MIN_ETA_IMPROVEMENT_SECONDS = 30

    try:
        if SIM is not None and hasattr(SIM, 'incidents') and SIM.incidents:
            # baseline: copy graph and restore original weights on incident edges
            H = ROAD_GRAPH.copy()
            for inc in SIM.incidents.values():
                e = inc.edge
                if e in H.edges:
                    if inc.original_weight is not None:
                        H.edges[e]["weight"] = inc.original_weight
            # compute baseline route
            base = shortest_path(H, source, target, by=by)
            baseline_eta = base.get("time_min")

            # alternative avoiding incident edges: heavily penalise incident edges
            H2 = ROAD_GRAPH.copy()
            for inc in SIM.incidents.values():
                e = inc.edge
                if e in H2.edges:
                    H2.edges[e]["weight"] = 1_000_000.0
            try:
                alt = shortest_path(H2, source, target, by=by)
            except Exception:
                alt = None

            # Compare current live ETA vs alternative avoiding incidents
            current_time = current.get("time_min")
            if current_time is not None and alt is not None:
                alt_time = alt.get("time_min")
                if alt_time is not None and (current_time - alt_time) * 60.0 >= MIN_ETA_IMPROVEMENT_SECONDS:
                    rerouted = True
                    recommended = alt
                    rerouting_reason = "incident_avoidance"

                # estimate CO2 saved by switching from current route to alt route
                def estimate_co2(route):
                    total_g = 0.0
                    coords = route.get("path", [])
                    for a, b in zip(coords[:-1], coords[1:]):
                        try:
                            u = int(a) if str(a).lstrip('-').isdigit() else a
                            v = int(b) if str(b).lstrip('-').isdigit() else b
                        except Exception:
                            u, v = a, b
                        if v in ROAD_GRAPH[u]:
                            d = min(ROAD_GRAPH[u][v].values(), key=lambda dd: dd.get('weight', dd.get('free_flow_time_min', 1.0)))
                            length_km = d.get('length', 0.0) / 1000.0
                            speed = d.get('current_speed_kmh', d.get('free_flow_speed_kmh', 30.0))
                            total_g += traffic_engine.co2_g_per_km(max(speed, 2.0)) * length_km
                    return total_g

                try:
                    orig_co2 = estimate_co2(current)
                    alt_co2 = estimate_co2(alt)
                    co2_saved_g = max(0.0, orig_co2 - alt_co2)
                    time_saved_min = max(0.0, current_time - alt_time)
                except Exception:
                    co2_saved_g = 0.0
    except Exception:
        baseline_eta = None

    out = {
        **current,
        "baseline_eta_min": baseline_eta,
        "recommended": bool(rerouted),
        "recommended_route": recommended,
        "time_saved_min": round(time_saved_min, 3),
        "co2_saved_g": round(co2_saved_g, 1),
        "rerouting_reason": rerouting_reason,
    }

    return jsonify(out)


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
    state = SIM.get_state()
    # attach latest CV metrics if available
    try:
        if DEFAULT_CV is not None:
            state['cv_metrics'] = DEFAULT_CV.get_metrics()
    except Exception:
        state['cv_metrics'] = None
    return jsonify(state)


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


@app.route("/api/demo/scenario", methods=["POST"])
def api_demo_scenario():
    """Trigger or clear a deterministic demo incident.

    Body: {"action": "trigger"} or {"action": "clear"}
    """
    body = request.get_json(silent=True) or {}
    action = body.get("action")
    if action == "trigger":
        # For the synthetic grid demo, pick a central horizontal edge so
        # the incident reliably sits on many shortest paths across the grid
        try:
            if USING_SYNTHETIC:
                # grid params are the defaults used by build_synthetic_grid
                rows = 8
                cols = 8
                mid_r = rows // 2
                mid_c = cols // 2
                u = mid_r * cols + mid_c
                v = mid_r * cols + (mid_c + 1)
                # find the exact key for the parallel edge
                k = next(iter(ROAD_GRAPH[u][v]))
                edge = (u, v, k)
                inc = SIM.trigger_incident(edge, incident_type="stalled_vehicle", severity=0.95, confidence=0.2, blocked_fraction=0.9)
                # recommend a demonstrative src/dst across the row
                demo_src = mid_r * cols + 0
                demo_dst = mid_r * cols + (cols - 1)
                return jsonify({"status": "triggered", "incident": inc.summary(), "demo_src": demo_src, "demo_dst": demo_dst})
            else:
                # fallback: pick the first routable edge
                for u, v, k in ROAD_GRAPH.edges(keys=True):
                    edge = (u, v, k)
                    try:
                        inc = SIM.trigger_incident(edge, incident_type="stalled_vehicle", severity=0.95, confidence=0.2, blocked_fraction=0.9)
                        return jsonify({"status": "triggered", "incident": inc.summary()})
                    except Exception:
                        continue
                return jsonify({"error": "no edge available to trigger incident"}), 500
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    elif action == "clear":
        n = SIM.clear_all_incidents()
        return jsonify({"status": "cleared", "cleared": n})
    else:
        return jsonify({"error": "action must be 'trigger' or 'clear'"}), 400


@app.route("/api/incidents")
def api_incidents():
    out = []
    if SIM is not None:
        # compute network baseline from SIM if available
        try:
            baseline_speed = getattr(SIM, '_cv_history') and (sum([s['average_speed_kmh'] for s in list(SIM._cv_history)[-SIM.cv_baseline_window:]]) / max(1, len(list(SIM._cv_history)[-SIM.cv_baseline_window:]))) or None
        except Exception:
            baseline_speed = None
        for inc in SIM.incidents.values():
            s = inc.summary()
            # per-incident observability: speed drop vs baseline and volume change if baseline known
            try:
                u, v, k = inc.edge
                edge_data = SIM.G[u][v][k]
                current_speed = edge_data.get('current_speed_kmh', edge_data.get('free_flow_speed_kmh', None))
                if baseline_speed and baseline_speed > 0 and current_speed is not None:
                    speed_drop_ratio = max(0.0, min(1.0, (baseline_speed - current_speed) / baseline_speed))
                else:
                    speed_drop_ratio = None
                s['speed_drop_ratio'] = speed_drop_ratio
            except Exception:
                s['speed_drop_ratio'] = None
            s['persistence_count'] = getattr(SIM, '_cv_persistent_count', 0)
            s['incident_confirmed'] = inc.detected_at is not None
            out.append(s)
    return jsonify({"incidents": out})


@app.route('/api/cv/metrics')
def api_cv_metrics():
    try:
        if DEFAULT_CV is None:
            return jsonify({'error': 'cv unavailable', 'metrics': None})
        return jsonify({'metrics': DEFAULT_CV.get_metrics(), 'mode': DEFAULT_CV.mode})
    except Exception as e:
        return jsonify({'error': str(e), 'metrics': None}), 500


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


@app.route("/api/drive/start", methods=["POST"])
def api_drive_start():
    """
    Body: {"source": <node id>, "target": <node id>}
    Starts (or replaces) the single 'drive from A to B' session, routed with
    traffic_engine.find_drive_route — A* with an admissible haversine
    heuristic plus a marginal-cost congestion term, so the route accounts
    for both this driver's own travel time AND the impact of adding this
    trip to already-congested links (see traffic_engine.py section 1b for
    the full rationale). Returns the same shape as GET /api/drive/state.
    """
    body = request.get_json(silent=True) or {}
    raw_source, raw_target = body.get("source"), body.get("target")
    if raw_source is None or raw_target is None:
        return jsonify({"error": "source and target node ids are required"}), 400
    source = int(raw_source) if str(raw_source).lstrip("-").isdigit() else raw_source
    target = int(raw_target) if str(raw_target).lstrip("-").isdigit() else raw_target
    if source not in ROAD_GRAPH or target not in ROAD_GRAPH:
        return jsonify({"error": "unknown source/target node id"}), 404

    state = SIM.start_drive(source, target)
    if not state.get("active") and state.get("status") not in ("arrived",):
        return jsonify({"error": "no route currently exists between these nodes"}), 404
    return jsonify(state)


@app.route("/api/drive/stop", methods=["POST"])
def api_drive_stop():
    SIM.stop_drive()
    return jsonify({"status": "stopped"})


@app.route("/api/drive/state")
def api_drive_state():
    """
    Polled continuously by the frontend while a drive is active. Along with
    the current edge's endpoints/enter/exit sim-times, this returns
    tick_seconds/sim_minutes_per_tick so the client can locally extrapolate
    the car's position between polls (smooth animation) instead of only
    ever snapping to a new spot once per poll.
    """
    return jsonify(SIM.get_drive_state())


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
# 5c. Admin control aliases + camera/scenario/peak-hour/demo-vehicle routes
# ---------------------------------------------------------------------------
@app.route("/api/edge/congestion", methods=["POST"])
def api_edge_congestion():
    """
    Body: {"u":.., "v":.., "k":.., "congestion_score": 0.0-1.0}
    Manual 'set congestion' admin control — forces this edge's congestion
    score instead of letting it be measured from live flow, until cleared
    (via /api/simulate/reset) or overwritten again. Same mechanism the
    camera analyzer below uses, just operator-driven instead of vision-driven.
    """
    body = request.get_json(silent=True) or {}
    edge = _find_edge(body.get("u"), body.get("v"), body.get("k"))
    if edge is None:
        return jsonify({"error": "no such edge"}), 404
    try:
        score = float(body["congestion_score"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "congestion_score (0-1) is required"}), 400
    ov = SIM.set_manual_congestion(edge, score, label=body.get("label", "manual override"))
    return jsonify({"edge": [str(x) for x in edge], "congestion_score": ov.manual_congestion_score})


@app.route("/api/edge/block", methods=["POST"])
def api_edge_block():
    """Body: {"u":.., "v":.., "k":.., "blocked": true|false} — alias of /api/simulate/closure."""
    body = request.get_json(silent=True) or {}
    edge = _find_edge(body.get("u"), body.get("v"), body.get("k"))
    if edge is None:
        return jsonify({"error": "no such edge"}), 404
    blocked = bool(body.get("blocked", True))
    SIM.set_road_closed(edge, blocked)
    return jsonify({"edge": [str(x) for x in edge], "blocked": blocked})


@app.route("/api/edge/zone", methods=["POST"])
def api_edge_zone_alias():
    """Body: {"u":.., "v":.., "k":.., "zone_type": ...} — alias of /api/simulate/zone."""
    return api_simulate_zone()


@app.route("/api/scenario/peak_hour", methods=["POST"])
def api_scenario_peak_hour():
    """
    Body: {"enabled": true}. City-wide policy toggle — while on, every
    hospital/school/emergency zone road gets an extra routing penalty on
    top of its normal zone weight (stacks with the existing always-on zone
    penalty; see traffic_engine.PEAK_HOUR_ZONE_EXTRA_MULTIPLIER). Distinct
    from /api/simulate/peak_surge, which is a temporary demand spike rather
    than a routing policy.
    """
    body = request.get_json(silent=True) or {}
    enabled = bool(body.get("enabled", not SIM.peak_hour_active))
    return jsonify({"peak_hour_active": SIM.set_peak_hour(enabled)})


@app.route("/api/scenario/reset", methods=["POST"])
def api_scenario_reset():
    """
    Clears every closure, jam/boost capacity override, and manual/camera
    congestion override city-wide, and turns peak hour off. Zone tags
    (hospital/school/emergency) are kept. Also clears the demo-vehicle batch
    (SIM's own live simulation and its auto-generated traffic are untouched
    — use /api/simulate/stop if you want to pause that separately).
    """
    SIM.reset_scenario()
    DEMO_VEHICLES.clear()
    return jsonify({"status": "reset"})


@app.route("/api/traffic/video", methods=["POST"])
def api_traffic_video():
    """
    Multipart form: `video` file + `u`, `v`, optional `k` node ids.
    Runs OpenCV motion-density analysis on the clip (see
    analyze_traffic_video) and applies the resulting congestion_score to
    that road segment via SIM.set_manual_congestion, same effect as a
    manual congestion update but sourced from real footage instead of a
    slider. The override auto-expires after 10 simulated minutes so a
    stale camera snapshot doesn't dictate routing forever — re-upload to
    refresh it.
    """
    if "video" not in request.files:
        return jsonify({"error": "multipart field 'video' (a file) is required"}), 400
    edge = _find_edge(request.form.get("u"), request.form.get("v"), request.form.get("k"))
    if edge is None:
        return jsonify({"error": "no such edge"}), 404
    try:
        analysis = analyze_traffic_video(request.files["video"])
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400

    SIM.set_manual_congestion(
        edge, analysis["estimated_congestion_score"], duration_min=10.0,
        label=f"camera: {analysis['avg_moving_objects']} obj/frame",
    )
    return jsonify({"edge": [str(x) for x in edge], **analysis})


# --- Standalone demo-vehicle simulation (spawn/tick/reset on demand) -------

@app.route("/api/simulation/spawn", methods=["POST"])
def api_demo_sim_spawn():
    """Body: {"count": 10} — spawns a tracked batch of demo vehicles."""
    body = request.get_json(silent=True) or {}
    count = max(1, min(200, int(body.get("count", 10))))
    spawned = demo_spawn_vehicles(count)
    return jsonify({"spawned": len(spawned), "vehicle_ids": spawned, "total_vehicles": len(DEMO_VEHICLES)})


@app.route("/api/simulation/tick", methods=["POST"])
def api_demo_sim_tick():
    """Body: {"dt_min": 0.5} — advances demo vehicles one step along live-weighted routes."""
    body = request.get_json(silent=True) or {}
    dt_min = float(body.get("dt_min", 0.5))
    return jsonify(demo_tick_simulation(dt_min=dt_min))


@app.route("/api/simulation/state")
def api_demo_sim_state():
    return jsonify({
        "tick": DEMO_SIM_TICK_COUNT,
        "vehicles": demo_vehicle_positions(),
        "total": len(DEMO_VEHICLES),
        "active": sum(1 for v in DEMO_VEHICLES.values() if v["status"] == "active"),
        "arrived": sum(1 for v in DEMO_VEHICLES.values() if v["status"] == "arrived"),
    })


@app.route("/api/simulation/reset", methods=["POST"])
def api_demo_sim_reset():
    DEMO_VEHICLES.clear()
    global DEMO_SIM_TICK_COUNT
    DEMO_SIM_TICK_COUNT = 0
    return jsonify({"status": "demo simulation reset"})


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