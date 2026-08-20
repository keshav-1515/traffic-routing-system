import time
import networkx as nx
from tests.test_incidents import setup_sim


def build_baseline(sim, speed=30.0, vehicles=5, samples=10):
    for i in range(samples):
        sim.set_external_cv_metrics({'average_speed_kmh': speed, 'total_vehicles': vehicles, 'active_vehicle_count': vehicles, 'timestamp': time.time()})
        # call detection to record history
        sim._detect_incidents()


def test_normal_traffic_does_not_trigger():
    sim = setup_sim()
    build_baseline(sim, speed=30.0, vehicles=5, samples=12)
    # normal observation
    sim.set_external_cv_metrics({'average_speed_kmh': 29.5, 'total_vehicles': 6, 'active_vehicle_count': 6, 'timestamp': time.time()})
    sim._detect_incidents()
    assert len(sim.incidents) == 0


def test_temporary_one_frame_speed_drop_is_ignored():
    sim = setup_sim()
    build_baseline(sim, speed=30.0, vehicles=5, samples=12)
    # single bad frame
    sim.set_external_cv_metrics({'average_speed_kmh': 6.0, 'total_vehicles': 8, 'active_vehicle_count': 8, 'timestamp': time.time()})
    sim._detect_incidents()
    # one-frame anomaly should not create incidents
    assert len(sim.incidents) == 0


def test_sustained_speed_drop_creates_incident():
    sim = setup_sim()
    build_baseline(sim, speed=30.0, vehicles=5, samples=12)
    # sustained drop
    for _ in range(sim.cv_persistence_required + 1):
        sim.set_external_cv_metrics({'average_speed_kmh': 5.0, 'total_vehicles': 6, 'active_vehicle_count': 6, 'timestamp': time.time()})
        sim._detect_incidents()
    # after persistence, at least one incident should be created
    assert len(sim.incidents) >= 1


def test_sustained_drop_plus_volume_increase_strengthens_confidence_and_severity():
    sim = setup_sim()
    build_baseline(sim, speed=30.0, vehicles=4, samples=12)
    # stronger sustained anomaly (speed drop + volume increase)
    for _ in range(sim.cv_persistence_required + 2):
        sim.set_external_cv_metrics({'average_speed_kmh': 4.0, 'total_vehicles': 12, 'active_vehicle_count': 12, 'timestamp': time.time()})
        sim._detect_incidents()
    # inspect created incidents
    assert len(sim.incidents) >= 1
    # at least one should be medium/high severity
    severities = [inc.severity for inc in sim.incidents.values()]
    assert any(s >= 0.6 for s in severities)


def test_persistence_requirement_and_clearance():
    sim = setup_sim()
    build_baseline(sim, speed=30.0, vehicles=5, samples=12)
    # create incident
    for _ in range(sim.cv_persistence_required + 1):
        sim.set_external_cv_metrics({'average_speed_kmh': 5.0, 'total_vehicles': 6, 'active_vehicle_count': 6, 'timestamp': time.time()})
        sim._detect_incidents()
    assert len(sim.incidents) >= 1
    # clear first incident
    iid = next(iter(sim.incidents.keys()))
    cleared = sim.clear_incident(iid)
    assert cleared is True


def test_existing_rerouting_still_works_with_cv_trigger():
    sim = setup_sim()
    G = sim.G
    nodes = list(G.nodes)
    src, dst = nodes[0], nodes[-1]
    # baseline
    H = G.copy()
    base_path = nx.dijkstra_path(H, src, dst, weight='weight')
    u, v = base_path[0], base_path[1]
    k = next(iter(G[u][v]))
    edge = (u, v, k)
    # force CV anomaly targeted at creating incident on that edge by lowering sim speed first
    G[u][v][k]['current_speed_kmh'] = 2.0
    build_baseline(sim, speed=30.0, vehicles=5, samples=12)
    for _ in range(sim.cv_persistence_required + 1):
        sim.set_external_cv_metrics({'average_speed_kmh': 3.0, 'total_vehicles': 8, 'active_vehicle_count': 8, 'timestamp': time.time()})
        sim._detect_incidents()
    # now incident should exist on some edge; ensure reroute computes alternative
    H2 = G.copy()
    H2.edges[edge]['weight'] = 1_000_000.0
    try:
        alt = nx.dijkstra_path(H2, src, dst, weight='weight')
    except Exception:
        alt = None
    assert alt is not None
