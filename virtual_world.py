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

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Global graph object — built once at startup, reused by every API call.
# This IS the actual routable data structure: a networkx.MultiDiGraph where
# every node is a real intersection and every edge is a real road segment.
# ---------------------------------------------------------------------------
ROAD_GRAPH = None
USING_SYNTHETIC = False


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


def init_graph(place=None, bbox=None, dist=1500):
    global ROAD_GRAPH, USING_SYNTHETIC
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
                "highway": str(data.get("highway", "unclassified")),
                "lanes": data.get("lanes"),
                "length_m": round(data.get("length", 0), 1),
                "free_flow_speed_kmh": data.get("free_flow_speed_kmh"),
                "capacity_veh_per_hr": data.get("capacity_veh_per_hr"),
                "free_flow_time_min": data.get("free_flow_time_min"),
                "weight": data.get("weight"),
                # stored on the graph now, not re-randomized per request
                "congestion_score": data.get("congestion_score"),
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
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    init_graph(place=args.place, bbox=args.bbox, dist=args.dist)
    app.run(host="0.0.0.0", port=args.port, debug=True, use_reloader=False)