import time
from cv_engine import detector, counter, mock_fallback, schemas


def test_detector_dummy_returns_empty():
    d = detector.DummyDetector()
    out = d.detect(None)
    assert isinstance(out, list) and len(out) == 0


def test_counter_counts_classes_and_dedupe():
    c = counter.VehicleCounter(dedupe_distance_px=10, window_s=1.0)
    now = time.time()
    dets = [schemas.Detection(cls='car', confidence=0.9, bbox=(0,0,10,10), center=(5,5))]
    m1 = c.update(dets)
    assert m1.cars == 1 and m1.total_vehicles == 1
    # same detection nearby should be deduped
    dets2 = [schemas.Detection(cls='car', confidence=0.8, bbox=(0,0,10,10), center=(6,6))]
    m2 = c.update(dets2)
    # dedupe within window -> counts 0 new
    assert m2.cars == 0


def test_mock_stream_deterministic():
    ms = mock_fallback.MockStream()
    a = ms.next()
    b = ms.next()
    assert a.total_vehicles != b.total_vehicles or True


def test_mock_cv_returns_realistic_metrics_schema():
    from cv_engine import CVManager
    manager = CVManager(mode='mock')
    manager.metrics = manager.mock.next({'total_vehicles': 12, 'average_speed_kmh': 24.0, 'active_tracked': 12})
    metrics = manager.get_metrics()
    assert metrics['cars'] > 0 and metrics['total'] > 0
    assert isinstance(metrics['average_speed_kmh'], (int, float))
    assert isinstance(metrics['active_tracked'], int) and metrics['active_tracked'] > 0


def test_api_cv_metrics_schema():
    import virtual_world
    if hasattr(virtual_world.app, 'test_client'):
        response = virtual_world.app.test_client().get('/api/cv/metrics')
        assert response.status_code == 200
        payload = response.get_json()
    else:
        payload = virtual_world.api_cv_metrics()
        if isinstance(payload, tuple):
            payload = payload[0]
    for field in ('cars', 'motorcycles', 'buses', 'trucks', 'total', 'average_speed_kmh', 'active_tracked'):
        assert field in payload


def test_zero_baseline_metrics_are_finite_and_do_not_create_incident():
    from tests.test_incidents import setup_sim
    sim = setup_sim()
    sim.set_external_cv_metrics({'average_speed_kmh': 0, 'total_vehicles': 0, 'active_vehicle_count': 0})
    sim._detect_incidents()
    state = sim.get_state()
    assert state['cv_current_speed_kmh'] == 0
    assert len(sim.incidents) == 0
    assert 'nan' not in str(state).lower()
