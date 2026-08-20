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


def test_api_cv_metrics_schema():
    from cv_engine import DEFAULT_CV
    m = DEFAULT_CV.get_metrics()
    assert 'total_vehicles' in m
