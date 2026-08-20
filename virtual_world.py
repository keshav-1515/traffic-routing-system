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

ROAD_GRAPH = None
USING_SYNTHETIC = False

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


def set_edge_congestion(G, u, v, k, congestion_score):
    data = G[u][v][k]
    data["congestion_score"] = max(0.0, min(1.0, congestion_score))
    recompute_weight(data)


def randomize_congestion(G, seed=None):
    if seed is not None:
        random.seed(seed)
    for _, _, _, data in G.edges(keys=True, data=True):
        data["congestion_score"] = round(random.uniform(0.05, 0.95), 2)
        recompute_weight(data)


def build_graph_from_place(place_name, dist=1500):
    import osmnx as ox

    print(f"[graph] Downloading road network for: {place_name}")
    try:
        G = ox.graph_from_place(place_name, network_type="drive", simplify=True)
    except Exception as e:
        print(f"[graph] graph_from_place failed ({e}); retrying as point + {dist}m radius", file=sys.stderr)
        G = ox.graph_from_address(place_name, dist=dist, network_type="drive", simplify=True)

    for _, _, _, data in G.edges(keys=True, data=True):
        enrich_edge(data)
    return G


def build_graph_from_bbox(north, south, east, west):
    import osmnx as ox
    print(f"[graph] Downloading road network for bbox N{north} S{south} E{east} W{west}")
    try:
        G = ox.graph_from_bbox(bbox=(west, south, east, north), network_type="drive", simplify=True)
    except TypeError:
        G = ox.graph_from_bbox(north, south, east, west, network_type="drive", simplify=True)
    for _, _, _, data in G.edges(keys=True, data=True):
        enrich_edge(data)
    return G


def build_synthetic_grid(rows=8, cols=8, spacing_m=120, origin_lat=12.9716, origin_lng=77.5946):
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

    if not USING_SYNTHETIC:
        randomize_congestion(ROAD_GRAPH)

    n_nodes = ROAD_GRAPH.number_of_nodes()
    n_edges = ROAD_GRAPH.number_of_edges()
    print(f"[graph] Ready: {n_nodes} nodes, {n_edges} directed edges ({'synthetic' if USING_SYNTHETIC else 'real OSM data'})")


def shortest_path(G, source, target, by="time"):
    weight_key = "weight" if by == "time" else "length"
    path = nx.dijkstra_path(G, source, target, weight=weight_key)

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
                "u": str(u),
                "v": str(v),
                "highway": str(data.get("highway", "unclassified")),
                "lanes": data.get("lanes"),
                "length_m": round(data.get("length", 0), 1),
                "free_flow_speed_kmh": data.get("free_flow_speed_kmh"),
                "capacity_veh_per_hr": data.get("capacity_veh_per_hr"),
                "free_flow_time_min": data.get("free_flow_time_min"),
                "weight": data.get("weight"),
                "congestion_score": data.get("congestion_score"),
            },
        })

    return {
        "nodes": {"type": "FeatureCollection", "features": node_features},
        "edges": {"type": "FeatureCollection", "features": edge_features},
    }


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
    mode = request.args.get("mode", default="car")
    if source is None or target is None:
        return jsonify({"error": "source and target query params are required"}), 400
    try:
        path = _astar_path(ROAD_GRAPH, source, target, by=by)
        result = _route_result_for_path(ROAD_GRAPH, path)
        result["algorithm"] = "A*"
        result["mode"] = mode
        if mode == "walking":
            result["time_min"] = round(result["distance_m"] / 1000 / 4.5 * 60, 2)
            result["congestion"] = 0.0
            result["predicted_congestion"] = 0.0
        return jsonify(result)
    except nx.NetworkXNoPath:
        return jsonify({"error": f"no path between {source} and {target}"}), 404
    except nx.NodeNotFound as e:
        return jsonify({"error": str(e)}), 404


def _all_road_features():
    return graph_to_geojson(ROAD_GRAPH)["edges"]["features"]


@app.route("/api/traffic")
def api_traffic():
    return jsonify({"roads": _all_road_features(), "demo": USING_SYNTHETIC})


@app.route("/api/traffic/predict", methods=["GET", "POST"])
def api_prediction():
    payload = request.get_json(silent=True) or {}
    road_id = str(payload.get("road_id") or request.args.get("road_id") or "road")
    horizon = int(payload.get("horizon_minutes") or request.args.get("horizon_minutes") or 15)
    roads = _all_road_features()
    current = 0.63
    for feature in roads:
        p = feature["properties"]
        if f"{p.get('u')}-{p.get('v')}" == road_id:
            current = float(p.get("congestion_score") or 0.63)
            break
    predicted = max(0.0, min(1.0, current + 0.08))
    return jsonify({"road_id": road_id, "current_congestion": current, "predicted_congestion": predicted, "horizon_minutes": horizon, "fallback": True, "note": "Heuristic fallback; replace with trained ML model."})


def _astar_path(G, source, target, by="time"):
    weight_key = "weight" if by == "time" else "length"
    def heuristic(a, b):
        ax, ay = G.nodes[a]["x"], G.nodes[a]["y"]
        bx, by_ = G.nodes[b]["x"], G.nodes[b]["y"]
        return ((ax-bx)**2 + (ay-by_)**2) ** 0.5
    return nx.astar_path(G, source, target, heuristic=heuristic, weight=weight_key)


def _route_result_for_path(G, path):
    total_length_m = 0.0
    total_time_min = 0.0
    congestion = []
    coords = []
    for u, v in zip(path[:-1], path[1:]):
        edge_data = min(G[u][v].values(), key=lambda d: d.get("weight", 0))
        total_length_m += edge_data.get("length", 0.0)
        total_time_min += edge_data.get("weight", 0.0)
        congestion.append(float(edge_data.get("congestion_score", 0.0)))
        if not coords:
            coords.append([G.nodes[u]["x"], G.nodes[u]["y"]])
        coords.append([G.nodes[v]["x"], G.nodes[v]["y"]])
    live = sum(congestion)/len(congestion) if congestion else 0.0
    predicted = min(1.0, live + 0.08)
    return {"path":[str(n) for n in path],"distance_m":round(total_length_m,1),"time_min":round(total_time_min,2),"congestion":live,"predicted_congestion":predicted,"geometry":{"type":"LineString","coordinates":coords}}


@app.route("/api/route-v2", methods=["POST"])
def api_route_v2():
    payload=request.get_json(silent=True) or {}
    source=payload.get("source")
    target=payload.get("target")
    mode=payload.get("mode","car")
    if source is None or target is None:
        return jsonify({"error":"source and target are required"}),400
    try:
        source=int(source); target=int(target)
        path=_astar_path(ROAD_GRAPH,source,target,by="time")
        result=_route_result_for_path(ROAD_GRAPH,path)
        if mode=="walking":
            result["time_min"]=round(result["distance_m"]/1000/4.5*60,2)
            result["congestion"]=0.0
            result["predicted_congestion"]=0.0
        return jsonify(result)
    except (nx.NetworkXNoPath,nx.NodeNotFound,ValueError) as e:
        return jsonify({"error":str(e)}),404


@app.route("/api/eco-route", methods=["POST"])
def api_eco_route():
    payload=request.get_json(silent=True) or {}
    source=payload.get("source"); target=payload.get("target")
    if source is None or target is None: return jsonify({"error":"source and target are required"}),400
    try:
        path=_astar_path(ROAD_GRAPH,int(source),int(target),by="time")
        result=_route_result_for_path(ROAD_GRAPH,path)
        car_km=result["distance_m"]/1000
        result["is_eco"]=True
        result["carbon_saved_kg"]=round(max(0.0,car_km*0.192-car_km*0.041),2)
        result["credits"]=25
        result["segments"]=[]
        result["fallback"]=True
        result["note"]="Demo eco route; connect real metro station data for multimodal routing."
        return jsonify(result)
    except Exception as e:
        return jsonify({"error":str(e)}),404


@app.route("/api/metro/stations")
def api_metro():
    nodes=list(ROAD_GRAPH.nodes(data=True))
    selected=nodes[::max(1,len(nodes)//6)] if nodes else []
    return jsonify({"stations":[{"id":str(n),"name":f"Metro Station {i+1}","lat":d["y"],"lng":d["x"]} for i,(n,d) in enumerate(selected[:6])]})


@app.route("/api/user/profile")
def api_profile():
    return jsonify({"username":"NavQ Commuter","email":"commuter@navq.app","preferred_mode":"car","eco_trips":12,"carbon_saved_kg":18.4})


@app.route("/api/user/credits")
def api_credits():
    return jsonify({"credits":250,"carbon_saved_kg":18.4,"eco_trips":12})


@app.route("/api/rewards")
def api_rewards():
    return jsonify({"rewards":[{"id":"coffee","title":"Coffee Coupon","description":"Green travel coffee reward.","cost_credits":100,"category":"Lifestyle","icon":"☕"},{"id":"voucher","title":"Travel Voucher","description":"Public transport travel voucher.","cost_credits":250,"category":"Transit","icon":"🎟️"},{"id":"premium","title":"Premium Reward","description":"Special commuter reward.","cost_credits":500,"category":"Premium","icon":"🎁"}]})


@app.route("/api/rewards/redeem", methods=["POST"])
def api_redeem():
    payload=request.get_json(silent=True) or {}
    return jsonify({"success":True,"reward_id":payload.get("reward_id")})


def _graph_center():
    lats = [d["y"] for _, d in ROAD_GRAPH.nodes(data=True)]
    lngs = [d["x"] for _, d in ROAD_GRAPH.nodes(data=True)]
    return sum(lats) / len(lats), sum(lngs) / len(lngs)


def parse_args():
    parser = argparse.ArgumentParser(description="City road-network graph viewer")
    parser.add_argument("--place", type=str, default=None, help='e.g. --place "Koramangala, Bangalore, India"')
    parser.add_argument("--bbox", type=float, nargs=4, default=None, metavar=("NORTH", "SOUTH", "EAST", "WEST"))
    parser.add_argument("--dist", type=int, default=1500)
    parser.add_argument("--port", type=int, default=5000)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    init_graph(place=args.place, bbox=args.bbox, dist=args.dist)
    app.run(host="0.0.0.0", port=args.port, debug=True, use_reloader=False)
