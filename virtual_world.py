"""
Traffic Map Backend - Complete working version
Run: python virtual_world.py --place "Koramangala, Bangalore, India"
"""

import argparse
import random
import sys
import tempfile
import uuid
import math

import networkx as nx
from flask import Flask, jsonify, render_template, request, send_from_directory

import traffic_engine

try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

app = Flask(__name__, static_folder='static', template_folder='templates')

# CORS headers
@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

# ---------------------------------------------------------------------------
# Global variables
# ---------------------------------------------------------------------------
DEMO_VEHICLES = {}
DEMO_SIM_TICK_COUNT = 0
ROAD_GRAPH = None
USING_SYNTHETIC = False
SIM: "traffic_engine.TrafficSimulation" = None

# ---------------------------------------------------------------------------
# Road profiles
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
CAPACITY_PER_LANE_PER_HOUR = 1800
BPR_ALPHA = 0.15
BPR_BETA = 4


def _road_profile(highway_tag):
    if isinstance(highway_tag, list):
        highway_tag = highway_tag[0]
    return ROAD_PROFILES.get(highway_tag, DEFAULT_PROFILE)


def enrich_edge(data):
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
    data["current_volume"] = 0
    if "congestion_score" not in data:
        data["congestion_score"] = round(random.uniform(0.05, 0.95), 2)
    recompute_weight(data)
    return data


def recompute_weight(data):
    penalty = 1 + BPR_ALPHA * (data.get("congestion_score", 0.0) ** BPR_BETA)
    data["weight"] = round(data["free_flow_time_min"] * penalty, 4)
    return data["weight"]


def randomize_congestion(G, seed=None):
    if seed is not None:
        random.seed(seed)
    for _, _, _, data in G.edges(keys=True, data=True):
        data["congestion_score"] = round(random.uniform(0.05, 0.95), 2)
        recompute_weight(data)


# ---------------------------------------------------------------------------
# Build graph
# ---------------------------------------------------------------------------
def build_graph_from_place(place_name, dist=1500):
    import osmnx as ox
    print(f"[graph] Downloading road network for: {place_name}")
    try:
        G = ox.graph_from_place(place_name, network_type="drive", simplify=True)
    except Exception as e:
        print(f"[graph] graph_from_place failed ({e}); retrying as point", file=sys.stderr)
        G = ox.graph_from_address(place_name, dist=dist, network_type="drive", simplify=True)
    for _, _, _, data in G.edges(keys=True, data=True):
        enrich_edge(data)
    return G


def build_graph_from_bbox(north, south, east, west):
    import osmnx as ox
    print(f"[graph] Downloading road network for bbox")
    try:
        G = ox.graph_from_bbox(bbox=(west, south, east, north), network_type="drive", simplify=True)
    except TypeError:
        G = ox.graph_from_bbox(north, south, east, west, network_type="drive", simplify=True)
    for _, _, _, data in G.edges(keys=True, data=True):
        enrich_edge(data)
    return G


def build_synthetic_grid(rows=8, cols=8, spacing_m=120, origin_lat=12.9716, origin_lng=77.5946):
    print(f"[graph] Building synthetic {rows}x{cols} grid")
    G = nx.MultiDiGraph()
    meters_per_deg = 111_320
    node_id = lambda r, c: r * cols + c

    for r in range(rows):
        for c in range(cols):
            lat = origin_lat + (r * spacing_m) / meters_per_deg
            lng = origin_lng + (c * spacing_m) / meters_per_deg
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

    if not USING_SYNTHETIC:
        randomize_congestion(ROAD_GRAPH)

    n_nodes = ROAD_GRAPH.number_of_nodes()
    n_edges = ROAD_GRAPH.number_of_edges()
    print(f"[graph] Ready: {n_nodes} nodes, {n_edges} edges")

    SIM = traffic_engine.TrafficSimulation(
        ROAD_GRAPH,
        recompute_weight_fn=recompute_weight,
        tick_seconds=tick_seconds,
        sim_minutes_per_tick=sim_minutes_per_tick,
        signal_mode=signal_mode,
    )
    if autostart:
        SIM.start()
        print(f"[sim] Started")


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
def shortest_path(G, source, target, by="time", avoid_nodes=None):
    avoid_nodes = avoid_nodes or set()
    weight_key = "weight" if by == "time" else "length"
    
    try:
        if avoid_nodes:
            G_copy = G.copy()
            for node in avoid_nodes:
                if node in G_copy.nodes:
                    G_copy.remove_node(node)
            path = nx.dijkstra_path(G_copy, source, target, weight=weight_key)
        else:
            path = nx.dijkstra_path(G, source, target, weight=weight_key)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        try:
            path = nx.dijkstra_path(G, source, target, weight=weight_key)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    total_length_m = 0.0
    total_time_min = 0.0
    coords = []

    for u, v in zip(path[:-1], path[1:]):
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
# GeoJSON
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
    seen = set()
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
                "u": str(u), "v": str(v), "k": str(k),
                "highway": str(data.get("highway", "unclassified")),
                "lanes": data.get("lanes"),
                "length_m": round(data.get("length", 0), 1),
                "free_flow_speed_kmh": data.get("free_flow_speed_kmh"),
                "capacity_veh_per_hr": data.get("capacity_veh_per_hr"),
                "free_flow_time_min": data.get("free_flow_time_min"),
                "weight": data.get("weight"),
                "congestion_score": data.get("congestion_score"),
                "current_speed_kmh": data.get("current_speed_kmh"),
                "current_volume": data.get("current_volume"),
                "queue_estimate": data.get("queue_estimate"),
                "signal_delay_min": data.get("signal_delay_min"),
                "incident": data.get("incident", False),
                "zone_type": data.get("zone_type"),
            },
        })

    return {
        "nodes": {"type": "FeatureCollection", "features": node_features},
        "edges": {"type": "FeatureCollection", "features": edge_features},
    }


# ---------------------------------------------------------------------------
# Flask Routes
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
    source = request.args.get("source", type=int)
    target = request.args.get("target", type=int)
    by = request.args.get("by", default="time")
    avoid_jams = request.args.get("avoid_jams", default="false").lower() == "true"
    
    if source is None or target is None:
        return jsonify({"error": "source and target required"}), 400
    
    avoid_nodes = set()
    if avoid_jams and SIM:
        avoid_nodes = SIM.jammed_nodes.copy()
    
    result = shortest_path(ROAD_GRAPH, source, target, by=by, avoid_nodes=avoid_nodes)
    if result is None:
        return jsonify({"error": "no path found"}), 404
    return jsonify(result)


def _graph_center():
    nodes = list(ROAD_GRAPH.nodes(data=True))
    if not nodes:
        return 12.9716, 77.5946
    lats = [d["y"] for _, d in nodes]
    lngs = [d["x"] for _, d in nodes]
    return sum(lats) / len(lats), sum(lngs) / len(lngs)


def _find_edge(u, v, k=None):
    u = int(u) if str(u).lstrip("-").isdigit() else u
    v = int(v) if str(v).lstrip("-").isdigit() else v
    if v not in ROAD_GRAPH[u]:
        return None
    if k is not None and k in ROAD_GRAPH[u][v]:
        return (u, v, k)
    return (u, v, next(iter(ROAD_GRAPH[u][v])))


@app.route("/api/simulate/state")
def api_simulate_state():
    return jsonify(SIM.get_state())


@app.route("/api/simulate/start", methods=["POST"])
def api_simulate_start():
    SIM.start()
    return jsonify({"running": SIM.running})


@app.route("/api/simulate/stop", methods=["POST"])
def api_simulate_stop():
    SIM.stop()
    return jsonify({"running": SIM.running})


@app.route("/api/simulate/jammed_nodes", methods=["GET"])
def api_jammed_nodes():
    if SIM is None:
        return jsonify({"jammed_nodes": [], "count": 0}), 500
    return jsonify({
        "jammed_nodes": [str(node) for node in SIM.jammed_nodes],
        "count": len(SIM.jammed_nodes),
        "sim_clock_min": SIM.sim_clock_min
    })


@app.route("/api/simulate/update_jams", methods=["POST"])
def api_update_jams():
    if SIM is None:
        return jsonify({"error": "Simulation not initialized"}), 500
    SIM._detect_jammed_nodes()
    return jsonify({
        "jammed_nodes": [str(node) for node in SIM.jammed_nodes],
        "count": len(SIM.jammed_nodes)
    })


@app.route("/api/simulate/create_jam", methods=["POST"])
def api_create_jam():
    if SIM is None:
        return jsonify({"error": "Simulation not initialized"}), 500
    
    body = request.get_json(silent=True) or {}
    node = body.get("node")
    duration_min = float(body.get("duration_min", 15.0))
    severity = float(body.get("severity", 0.8))
    
    if node is None:
        return jsonify({"error": "node is required"}), 400
    
    try:
        node = int(node) if str(node).lstrip("-").isdigit() else node
    except ValueError:
        return jsonify({"error": "invalid node id"}), 400
    
    if node not in ROAD_GRAPH:
        return jsonify({"error": "node not found"}), 404
    
    incoming_edges = [(u, node, k) for u, _, k in ROAD_GRAPH.in_edges(node, keys=True)]
    
    if not incoming_edges:
        return jsonify({"error": "no incoming edges"}), 400
    
    for edge in incoming_edges:
        SIM.set_manual_congestion(
            edge, 
            congestion_score=severity,
            duration_min=duration_min,
            label=f"node_jam_at_{node}"
        )
    
    SIM._detect_jammed_nodes()
    
    return jsonify({
        "node": str(node),
        "jammed_edges": len(incoming_edges),
        "duration_min": duration_min,
        "severity": severity,
        "jammed_nodes": [str(n) for n in SIM.jammed_nodes]
    })


@app.route("/api/simulate/vehicles", methods=["GET"])
def api_vehicles():
    limit = request.args.get("limit", default=400, type=int)
    sample_every = request.args.get("sample_every", default=1, type=int)
    return jsonify(SIM.get_vehicle_positions(limit=limit, sample_every=sample_every))


@app.route("/api/simulate/node", methods=["GET"])
def api_node_info():
    node_id = request.args.get("id")
    if node_id is None:
        return jsonify({"error": "id required"}), 400
    try:
        node = int(node_id) if str(node_id).lstrip("-").isdigit() else node_id
    except ValueError:
        return jsonify({"error": "invalid node id"}), 400
    info = SIM.get_node_info(node)
    if info is None:
        return jsonify({"error": "node not found"}), 404
    return jsonify(info)


@app.route("/api/simulate/jam_edge", methods=["POST"])
def api_jam_edge():
    body = request.get_json(silent=True) or {}
    edge = _find_edge(body.get("u"), body.get("v"), body.get("k"))
    if edge is None:
        return jsonify({"error": "no such edge"}), 404
    duration = float(body.get("duration_min", 15.0))
    severity = float(body.get("severity", 0.8))
    ov = SIM.set_manual_congestion(edge, severity, duration_min=duration, label="manual_jam")
    return jsonify({"edge": [str(x) for x in edge], "severity": severity, "duration": duration})


@app.route("/api/simulate/clear_jam", methods=["POST"])
def api_clear_jam():
    body = request.get_json(silent=True) or {}
    edge = _find_edge(body.get("u"), body.get("v"), body.get("k"))
    if edge is None:
        return jsonify({"error": "no such edge"}), 404
    SIM.reset_edge(edge)
    return jsonify({"edge": [str(x) for x in edge], "cleared": True})


@app.route("/api/simulate/signals", methods=["GET"])
def api_signals():
    signals = {}
    for node, sig in SIM.signals.items():
        signals[str(node)] = sig.get_current_signal_state()
    return jsonify({"signals": signals})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--place", type=str, default=None)
    parser.add_argument("--bbox", type=float, nargs=4, default=None)
    parser.add_argument("--dist", type=int, default=1500)
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--signal-mode", choices=["fixed", "adaptive"], default="adaptive")
    parser.add_argument("--tick-seconds", type=float, default=2.0)
    parser.add_argument("--sim-minutes-per-tick", type=float, default=1.0)
    parser.add_argument("--no-autostart", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    init_graph(place=args.place, bbox=args.bbox, dist=args.dist,
               signal_mode=args.signal_mode, tick_seconds=args.tick_seconds,
               sim_minutes_per_tick=args.sim_minutes_per_tick,
               autostart=not args.no_autostart)
    app.run(host="0.0.0.0", port=args.port, debug=True, use_reloader=False, threaded=True)