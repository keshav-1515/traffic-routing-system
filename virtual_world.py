"""
Traffic Control Server
-----------------------
Core responsibilities (ALL logic lives here in Python):
  1. Download / build a real road network for a given place using OSMnx.
  2. Convert it into a NetworkX graph (nodes = intersections, edges = road segments).
  3. Enrich every edge with attributes needed for congestion modelling
     (length, free-flow speed, estimated capacity, free-flow travel time,
     congestion_score, blocked flag, special-zone tag, and a routable `weight`).
  4. Serve the graph as GeoJSON over a Flask API.
  5. Let an administrator push live changes onto the graph: set congestion on
     a road, block/unblock it, tag it as a hospital/school zone, flip the
     city into "peak hour" (which penalizes routes through those zones),
     or feed it a traffic video that gets analyzed with OpenCV to estimate
     vehicle density.
  6. Run a lightweight in-server vehicle simulation: spawn vehicles with a
     source/destination, move them along Dijkstra-optimal routes, have them
     add to congestion as they drive, and have them re-route as conditions
     change.
  7. Serve a single HTML dashboard (Leaflet map) that renders all of the
     above and lets the admin drive it — the frontend does ZERO routing or
     congestion math, it only draws what Python sends it and posts back the
     admin's actions.

IMPORTANT: this script ONLY draws a real road network if you give it a real
place or bounding box. Without one, it falls back to a synthetic grid with
made-up coordinates, which will NEVER line up with real map tiles.

Run:
    python virtual_world.py --place "Koramangala, Bangalore, India"
    python virtual_world.py --bbox 12.935 12.925 77.635 77.615   # north south east west
    python virtual_world.py                                     # synthetic grid (offline demo only)

Extra dependency for the video-based traffic analysis endpoint:
    pip install opencv-python-headless numpy
(the rest of the app works fine without it — that one endpoint just reports
an error until it's installed).
"""

import argparse
import random
import sys
import tempfile
import time
import uuid

import networkx as nx
from flask import Flask, jsonify, render_template, request

try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Global graph object — built once at startup, reused by every API call.
# This IS the actual routable data structure: a networkx.MultiDiGraph where
# every node is a real intersection and every edge is a real road segment.
# ---------------------------------------------------------------------------
ROAD_GRAPH = None
USING_SYNTHETIC = False

# City-wide scenario flags the admin dashboard can flip live.
PEAK_HOUR = False

# In-memory vehicle simulation state. Keyed by vehicle id.
VEHICLES = {}
SIM_TICK_COUNT = 0


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

# Extra multiplier applied to hospital/school-tagged roads while PEAK_HOUR is
# on, so the routing model steers traffic away from sensitive zones exactly
# when it would hurt most.
ZONE_PEAK_PENALTY_MULTIPLIER = 3.0
VALID_ZONE_TYPES = {None, "hospital", "school", "emergency"}

# How much a vehicle physically sitting on a road should push its
# congestion_score up, on top of the "ambient" congestion already there.
VEHICLE_LOAD_WEIGHT = 0.5


def _road_profile(highway_tag):
    """OSM 'highway' tag can be a string or a list of strings — normalise it."""
    if isinstance(highway_tag, list):
        highway_tag = highway_tag[0]
    return ROAD_PROFILES.get(highway_tag, DEFAULT_PROFILE)


def enrich_edge(data):
    """
    Attach every attribute the routing/congestion/simulation engine needs,
    directly on the graph edge (not computed later in the API layer). This
    means the SAME graph object can be handed straight to nx.dijkstra_path().
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

    # current_volume = number of simulated vehicles currently on this edge.
    # Populated live by the vehicle simulation loop (see tick_simulation).
    data.setdefault("current_volume", 0)
    if "congestion_score" not in data:
        data["congestion_score"] = round(random.uniform(0.05, 0.95), 2)

    # Admin-controllable state.
    data.setdefault("blocked", False)
    data.setdefault("zone_type", None)  # None | "hospital" | "school" | "emergency"

    recompute_weight(data)
    return data


def recompute_weight(data):
    """
    Turn (distance, congestion, blockage, zone/peak-hour policy) into a
    single routable cost, stored as `weight` directly on the edge. This is
    what Dijkstra/A* uses as the `weight=` argument.

    weight_min = free_flow_time_min * (1 + ALPHA * congestion_score^BETA)
                 * (zone_peak_multiplier if applicable)

    A blocked road becomes effectively unroutable (infinite weight) without
    having to delete it from the graph, so it can be unblocked again later.
    """
    if data.get("blocked"):
        data["weight"] = float("inf")
        return data["weight"]

    penalty = 1 + BPR_ALPHA * (data.get("congestion_score", 0.0) ** BPR_BETA)
    weight = data["free_flow_time_min"] * penalty

    if PEAK_HOUR and data.get("zone_type") in ("hospital", "school", "emergency"):
        weight *= ZONE_PEAK_PENALTY_MULTIPLIER

    data["weight"] = round(weight, 4)
    return data["weight"]


def set_edge_congestion(G, u, v, k, congestion_score):
    """Update congestion on a single edge and keep its `weight` in sync."""
    data = G[u][v][k]
    data["congestion_score"] = max(0.0, min(1.0, congestion_score))
    recompute_weight(data)


def randomize_congestion(G, seed=None):
    """
    Assign a congestion_score (and derived weight) to every edge ONCE.
    Congestion is stored on the graph itself, so it's stable across API
    calls and consistent between what's shown on the map and what Dijkstra
    routes against. Call again to "reset" the city to a fresh random state.
    """
    if seed is not None:
        random.seed(seed)
    for _, _, _, data in G.edges(keys=True, data=True):
        data["congestion_score"] = round(random.uniform(0.05, 0.95), 2)
        recompute_weight(data)


def _all_edges_between(u, v):
    """
    Return every parallel MultiDiGraph edge connecting u and v, in EITHER
    direction. The dashboard treats a "road segment" as the thing a user
    clicks on the map, which is really the undirected u<->v pair (see
    graph_to_geojson's dedupe), so admin actions apply to both directions.
    """
    edges = []
    if ROAD_GRAPH.has_edge(u, v):
        for k in ROAD_GRAPH[u][v]:
            edges.append((u, v, k, ROAD_GRAPH[u][v][k]))
    if ROAD_GRAPH.has_edge(v, u):
        for k in ROAD_GRAPH[v][u]:
            edges.append((v, u, k, ROAD_GRAPH[v][u][k]))
    return edges


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

    # Sprinkle in a couple of "special zones" (hospital / school) on the
    # synthetic grid so the peak-hour routing policy has something to do
    # even in offline demo mode.
    demo_zone_edges = [
        (node_id(2, 2), node_id(2, 3), "hospital"),
        (node_id(5, 5), node_id(5, 6), "school"),
    ]
    for a, b, zone in demo_zone_edges:
        for u, v in ((a, b), (b, a)):
            if G.has_edge(u, v):
                for k in G[u][v]:
                    G[u][v][k]["zone_type"] = zone
                    recompute_weight(G[u][v][k])

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

    by="time"     -> uses the congestion+zone-policy-adjusted `weight`
                     attribute (realistic ETA-style routing that already
                     avoids blocked roads and, during peak hour, avoids
                     hospital/school zones)
    by="distance" -> uses raw `length` in metres (shortest physical route,
                     ignores congestion but still avoids blocked roads)

    Returns dict with the node path, total distance (m), total time (min),
    and a GeoJSON LineString for drawing the route on the map.
    """
    if by == "distance":
        # Still must not route through blocked roads: give them effectively
        # infinite length rather than their real (short) length.
        def dist_weight(u, v, d):
            return float("inf") if d.get("blocked") else d.get("length", 0.0)
        path = nx.dijkstra_path(G, source, target, weight=dist_weight)
        weight_key = "length"
    else:
        path = nx.dijkstra_path(G, source, target, weight="weight")
        weight_key = "weight"

    total_length_m = 0.0
    total_time_min = 0.0
    coords = []

    for u, v in zip(path[:-1], path[1:]):
        # pick the cheapest usable parallel edge between u and v (MultiDiGraph)
        candidates = [d for d in G[u][v].values() if not d.get("blocked")] or list(G[u][v].values())
        edge_data = min(candidates, key=lambda d: d.get(weight_key, 0.0))
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
# 4. Vehicle simulation — spawn vehicles, move them along live-optimal
#    routes, and let their presence feed back into congestion.
# ---------------------------------------------------------------------------
def _random_routable_pair():
    """Pick a random (source, target) pair that actually has a path."""
    nodes = list(ROAD_GRAPH.nodes)
    for _ in range(50):
        a, b = random.sample(nodes, 2)
        if nx.has_path(ROAD_GRAPH, a, b):
            return a, b
    raise RuntimeError("Could not find a routable node pair in the graph")


def spawn_vehicles(count):
    spawned = []
    for _ in range(count):
        try:
            source, target = _random_routable_pair()
            path = nx.dijkstra_path(ROAD_GRAPH, source, target, weight="weight")
        except (RuntimeError, nx.NetworkXNoPath):
            continue
        vid = uuid.uuid4().hex[:8]
        vehicle = {
            "id": vid,
            "source": source,
            "target": target,
            "path": path,
            "edge_index": 0,       # which hop of `path` the vehicle is on
            "progress": 0.0,       # 0..1 progress along the current hop
            "status": "active",    # active | arrived
            "color": random.choice(["#3388ff", "#8e44ad", "#16a085", "#e67e22", "#2c3e50"]),
        }
        VEHICLES[vid] = vehicle
        spawned.append(vid)
    return spawned


def _reroute_vehicle(vehicle):
    """Recompute the remaining route from the vehicle's current node onward,
    using the LATEST weights — this is what makes vehicles react to newly
    blocked roads / new congestion / peak-hour zone penalties instead of
    blindly following a route decided once at spawn time."""
    path = vehicle["path"]
    current_node = path[vehicle["edge_index"]]
    try:
        new_path = nx.dijkstra_path(ROAD_GRAPH, current_node, vehicle["target"], weight="weight")
    except nx.NetworkXNoPath:
        return  # stuck for now (e.g. everything around it just got blocked)
    vehicle["path"] = new_path
    vehicle["edge_index"] = 0


def tick_simulation(dt_min=0.5, reroute_every=4):
    """
    Advance the simulation by dt_min minutes of simulated time:
      1. Zero out current_volume on every edge.
      2. For each active vehicle, recompute which edge it's on and add its
         weight to that edge's current_volume.
      3. Blend that live occupancy into each loaded edge's congestion_score
         (so heavily-driven roads visibly get more congested), then
         recompute weight.
      4. Move each vehicle forward along its route by dt_min, periodically
         re-routing it against the freshly updated weights.
    """
    global SIM_TICK_COUNT
    SIM_TICK_COUNT += 1

    for _, _, _, data in ROAD_GRAPH.edges(keys=True, data=True):
        data["current_volume"] = 0

    active_vehicles = [v for v in VEHICLES.values() if v["status"] == "active"]

    # Periodic re-routing so vehicles react to changing conditions instead
    # of committing to their spawn-time path forever.
    if SIM_TICK_COUNT % reroute_every == 0:
        for vehicle in active_vehicles:
            _reroute_vehicle(vehicle)

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
        edge_data["current_volume"] = edge_data.get("current_volume", 0) + 1

        edge_time_min = edge_data.get("free_flow_time_min", 0.1) or 0.1
        vehicle["progress"] += dt_min / max(edge_time_min, 0.05)

        if vehicle["progress"] >= 1.0:
            vehicle["progress"] = 0.0
            vehicle["edge_index"] += 1
            if vehicle["edge_index"] >= len(path) - 1:
                vehicle["status"] = "arrived"

    # Feed live occupancy back into congestion for every loaded edge.
    for _, _, _, data in ROAD_GRAPH.edges(keys=True, data=True):
        if data.get("blocked"):
            continue
        capacity_slots = max(data.get("lanes", 1) * 3, 1)  # rough "roads feel full" threshold
        occupancy_ratio = min(1.0, data["current_volume"] / capacity_slots)
        if data["current_volume"] > 0:
            base = data.get("congestion_score", 0.0)
            data["congestion_score"] = round(
                min(1.0, base * (1 - VEHICLE_LOAD_WEIGHT) + occupancy_ratio * VEHICLE_LOAD_WEIGHT), 3
            )
            recompute_weight(data)

    return {
        "tick": SIM_TICK_COUNT,
        "active": len(active_vehicles),
        "arrived": sum(1 for v in VEHICLES.values() if v["status"] == "arrived"),
    }


def vehicle_positions():
    """Interpolate every vehicle's current lat/lng along its current edge,
    for the frontend to render as moving markers."""
    positions = []
    for vehicle in VEHICLES.values():
        path = vehicle["path"]
        idx = min(vehicle["edge_index"], len(path) - 2) if len(path) > 1 else 0
        if len(path) < 2:
            continue
        u, v = path[idx], path[idx + 1]
        if u not in ROAD_GRAPH.nodes or v not in ROAD_GRAPH.nodes:
            continue
        u_data, v_data = ROAD_GRAPH.nodes[u], ROAD_GRAPH.nodes[v]
        t = vehicle["progress"] if vehicle["status"] == "active" else 1.0
        lng = u_data["x"] + (v_data["x"] - u_data["x"]) * t
        lat = u_data["y"] + (v_data["y"] - u_data["y"]) * t
        positions.append({
            "id": vehicle["id"],
            "lat": lat,
            "lng": lng,
            "status": vehicle["status"],
            "color": vehicle["color"],
            "target": str(vehicle["target"]),
        })
    return positions


# ---------------------------------------------------------------------------
# 5. Computer-vision traffic analysis (lightweight motion-density estimate)
# ---------------------------------------------------------------------------
def analyze_traffic_video(file_storage):
    """
    Estimate a congestion_score from an uploaded traffic video using classic
    OpenCV background subtraction + contour counting — a real, dependency-
    light motion-density heuristic (moving blobs above a size threshold are
    treated as vehicles). This is intentionally simpler than a full
    YOLO + DeepSORT pipeline (no model weights, runs anywhere OpenCV runs);
    swapping in a proper detector/tracker later is a drop-in replacement
    for this function's internals, everything downstream (the edge update)
    stays the same.
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
# 6. Convert the NetworkX graph into GeoJSON the frontend can render directly
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
                "weight": None if data.get("weight") == float("inf") else data.get("weight"),
                "congestion_score": data.get("congestion_score"),
                "current_volume": data.get("current_volume", 0),
                "blocked": bool(data.get("blocked", False)),
                "zone_type": data.get("zone_type"),
            },
        })

    return {
        "nodes": {"type": "FeatureCollection", "features": node_features},
        "edges": {"type": "FeatureCollection", "features": edge_features},
    }


# ---------------------------------------------------------------------------
# 7. Flask routes
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
        "peak_hour": PEAK_HOUR,
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


# --- Admin: live traffic control -------------------------------------------

def _parse_uv():
    body = request.get_json(force=True, silent=True) or {}
    try:
        u, v = int(body["u"]), int(body["v"])
    except (KeyError, TypeError, ValueError):
        return None, None, jsonify({"error": "u and v (node ids) are required"}), 400
    return u, v, body, None


@app.route("/api/edge/congestion", methods=["POST"])
def api_edge_congestion():
    """Body: {"u": 1, "v": 2, "congestion_score": 0.0-1.0}"""
    u, v, body, err = _parse_uv()
    if err:
        return err, 400
    try:
        score = float(body["congestion_score"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "congestion_score (0-1) is required"}), 400

    edges = _all_edges_between(u, v)
    if not edges:
        return jsonify({"error": f"no road segment between {u} and {v}"}), 404
    for _, _, _, data in edges:
        data["congestion_score"] = max(0.0, min(1.0, score))
        recompute_weight(data)
    return jsonify({"updated_edges": len(edges), "congestion_score": max(0.0, min(1.0, score))})


@app.route("/api/edge/block", methods=["POST"])
def api_edge_block():
    """Body: {"u": 1, "v": 2, "blocked": true}"""
    u, v, body, err = _parse_uv()
    if err:
        return err, 400
    blocked = bool(body.get("blocked", True))

    edges = _all_edges_between(u, v)
    if not edges:
        return jsonify({"error": f"no road segment between {u} and {v}"}), 404
    for _, _, _, data in edges:
        data["blocked"] = blocked
        recompute_weight(data)
    return jsonify({"updated_edges": len(edges), "blocked": blocked})


@app.route("/api/edge/zone", methods=["POST"])
def api_edge_zone():
    """Body: {"u": 1, "v": 2, "zone_type": "hospital" | "school" | "emergency" | null}"""
    u, v, body, err = _parse_uv()
    if err:
        return err, 400
    zone_type = body.get("zone_type")
    if zone_type not in VALID_ZONE_TYPES:
        return jsonify({"error": f"zone_type must be one of {sorted(str(z) for z in VALID_ZONE_TYPES)}"}), 400

    edges = _all_edges_between(u, v)
    if not edges:
        return jsonify({"error": f"no road segment between {u} and {v}"}), 404
    for _, _, _, data in edges:
        data["zone_type"] = zone_type
        recompute_weight(data)
    return jsonify({"updated_edges": len(edges), "zone_type": zone_type})


@app.route("/api/scenario/peak_hour", methods=["POST"])
def api_peak_hour():
    """Body: {"enabled": true}. Re-scores every edge against the new policy."""
    global PEAK_HOUR
    body = request.get_json(force=True, silent=True) or {}
    PEAK_HOUR = bool(body.get("enabled", not PEAK_HOUR))
    for _, _, _, data in ROAD_GRAPH.edges(keys=True, data=True):
        recompute_weight(data)
    return jsonify({"peak_hour": PEAK_HOUR})


@app.route("/api/scenario/reset", methods=["POST"])
def api_scenario_reset():
    """Randomizes congestion city-wide and clears all blocks (keeps zone tags)."""
    randomize_congestion(ROAD_GRAPH)
    for _, _, _, data in ROAD_GRAPH.edges(keys=True, data=True):
        data["blocked"] = False
        recompute_weight(data)
    VEHICLES.clear()
    return jsonify({"status": "reset"})


@app.route("/api/traffic/video", methods=["POST"])
def api_traffic_video():
    """
    Multipart form: `video` file + `u`, `v` node ids.
    Runs OpenCV motion-density analysis on the clip and applies the
    resulting congestion_score to that road segment, same as a manual
    congestion update but sourced from real footage instead of a slider.
    """
    if "video" not in request.files:
        return jsonify({"error": "multipart field 'video' (a file) is required"}), 400
    try:
        u, v = int(request.form["u"]), int(request.form["v"])
    except (KeyError, ValueError):
        return jsonify({"error": "form fields u and v (node ids) are required"}), 400

    edges = _all_edges_between(u, v)
    if not edges:
        return jsonify({"error": f"no road segment between {u} and {v}"}), 404

    try:
        analysis = analyze_traffic_video(request.files["video"])
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400

    for _, _, _, data in edges:
        data["congestion_score"] = analysis["estimated_congestion_score"]
        recompute_weight(data)

    return jsonify({"updated_edges": len(edges), **analysis})


# --- Vehicle simulation ------------------------------------------------------

@app.route("/api/simulation/spawn", methods=["POST"])
def api_sim_spawn():
    """Body: {"count": 10}"""
    body = request.get_json(force=True, silent=True) or {}
    count = max(1, min(200, int(body.get("count", 10))))
    spawned = spawn_vehicles(count)
    return jsonify({"spawned": len(spawned), "vehicle_ids": spawned, "total_vehicles": len(VEHICLES)})


@app.route("/api/simulation/tick", methods=["POST"])
def api_sim_tick():
    body = request.get_json(force=True, silent=True) or {}
    dt_min = float(body.get("dt_min", 0.5))
    stats = tick_simulation(dt_min=dt_min)
    return jsonify(stats)


@app.route("/api/simulation/state")
def api_sim_state():
    return jsonify({
        "tick": SIM_TICK_COUNT,
        "vehicles": vehicle_positions(),
        "total": len(VEHICLES),
        "active": sum(1 for v in VEHICLES.values() if v["status"] == "active"),
        "arrived": sum(1 for v in VEHICLES.values() if v["status"] == "arrived"),
    })


@app.route("/api/simulation/reset", methods=["POST"])
def api_sim_reset():
    VEHICLES.clear()
    global SIM_TICK_COUNT
    SIM_TICK_COUNT = 0
    for _, _, _, data in ROAD_GRAPH.edges(keys=True, data=True):
        data["current_volume"] = 0
    return jsonify({"status": "simulation reset"})


def _graph_center():
    lats = [d["y"] for _, d in ROAD_GRAPH.nodes(data=True)]
    lngs = [d["x"] for _, d in ROAD_GRAPH.nodes(data=True)]
    return sum(lats) / len(lats), sum(lngs) / len(lngs)


# ---------------------------------------------------------------------------
# 8. CLI entrypoint
# ---------------------------------------------------------------------------
def parse_args():
    parser = argparse.ArgumentParser(description="City road-network traffic control server")
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
