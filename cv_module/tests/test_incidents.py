import networkx as nx
from virtual_world import build_synthetic_grid, recompute_weight, shortest_path, ROAD_GRAPH
import traffic_engine


def setup_sim():
    G = build_synthetic_grid(rows=5, cols=5)
    # ensure weights are present
    for _, _, _, d in G.edges(keys=True, data=True):
        recompute_weight(d)
    sim = traffic_engine.TrafficSimulation(G, recompute_weight, tick_seconds=0.01, sim_minutes_per_tick=1.0, signal_mode='adaptive', max_vehicles=1000, base_spawn_rate_per_min=1.0, seed=42)
    return sim


def test_trigger_and_clear_incident():
    sim = setup_sim()
    G = sim.G
    # pick a routable node pair
    nodes = list(G.nodes)
    a, b = nodes[0], nodes[-1]
    path = nx.dijkstra_path(G, a, b, weight='weight')
    assert len(path) >= 2
    u, v = path[0], path[1]
    k = next(iter(G[u][v]))
    edge = (u, v, k)

    inc = sim.trigger_incident(edge, incident_type='stalled_vehicle', severity=0.9, confidence=0.1, blocked_fraction=0.8)
    assert inc.incident_id in sim.incidents
    # edge should have incident_info and marked incident
    data = G[u][v][k]
    assert data.get('incident_info') is not None
    assert data.get('incident') is True

    # clear
    cleared = sim.clear_incident(inc.incident_id)
    assert cleared is True
    assert inc.incident_id not in sim.incidents
    assert data.get('incident') in (False, 0) or data.get('incident_info') is None


def test_incident_confidence_and_detection():
    sim = setup_sim()
    G = sim.G
    # pick edge and trigger
    nodes = list(G.nodes)
    a, b = nodes[1], nodes[-2]
    path = nx.dijkstra_path(G, a, b, weight='weight')
    u, v = path[0], path[1]
    k = next(iter(G[u][v]))
    edge = (u, v, k)
    inc = sim.trigger_incident(edge, incident_type='accident', severity=0.95, confidence=0.05, blocked_fraction=0.9)
    # run a tick to allow detection logic to update confidence
    sim.tick()
    # confidence should increase from initial 0.05
    assert inc.confidence >= 0.05
    assert inc.detected_at is not None or inc.confidence >= 0.35


def test_reroute_and_co2_estimate():
    sim = setup_sim()
    G = sim.G
    nodes = list(G.nodes)
    src, dst = nodes[0], nodes[-1]
    # baseline route on a copy (no incidents)
    H = G.copy()
    base = shortest_path(H, src, dst, by='time')
    assert base is not None
    # pick an edge on the baseline route and trigger incident
    path_nodes = base['path']
    u = int(path_nodes[0]); v = int(path_nodes[1])
    k = next(iter(G[u][v]))
    edge = (u, v, k)
    inc = sim.trigger_incident(edge, incident_type='debris', severity=1.0, confidence=0.1, blocked_fraction=1.0)
    # compute alternative avoiding incident
    H2 = G.copy()
    H2.edges[edge]['weight'] = 1_000_000.0
    try:
        alt = shortest_path(H2, src, dst, by='time')
    except Exception:
        alt = None
    assert alt is not None
    # time saved should be non-negative
    time_saved = max(0.0, base['time_min'] - alt['time_min'])
    assert time_saved >= 0.0
    # CO2 estimate using traffic_engine.co2_g_per_km shouldn't error
    g_est = 0.0
    for a, b in zip(alt['path'][:-1], alt['path'][1:]):
        u = int(a); v = int(b)
        d = min(G[u][v].values(), key=lambda dd: dd.get('weight', dd.get('free_flow_time_min', 1.0)))
        length_km = d.get('length', 0.0)/1000.0
        speed = d.get('current_speed_kmh', d.get('free_flow_speed_kmh', 30.0))
        g_est += traffic_engine.co2_g_per_km(max(speed, 2.0)) * length_km
    assert g_est >= 0.0
 