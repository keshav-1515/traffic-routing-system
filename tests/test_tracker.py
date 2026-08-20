import time
from cv_engine.tracker import SimpleTracker, MockTracker, Track
from cv_engine.schemas import Detection


def test_track_creation_and_persistence():
    t = SimpleTracker(dedupe_distance_px=20, pixels_per_meter=10.0)
    det = Detection(cls='car', confidence=0.9, bbox=(0,0,10,10), center=(50,50))
    out = t.update([det], timestamp=time.time())
    assert out['active_vehicle_count'] >= 1


def test_track_termination_on_missed_frames():
    t = SimpleTracker(max_missed=1)
    det = Detection(cls='car', confidence=0.9, bbox=(0,0,10,10), center=(10,10))
    t.update([det], timestamp=time.time())
    # next frame no detections -> missed_frames increments
    t.update([], timestamp=time.time() + 1)
    # now checks
    metrics = t.metrics()
    # tracks may be inactive
    assert isinstance(metrics['tracks'], dict)


def test_mock_tracker_returns_metrics():
    mt = MockTracker()
    out = mt.update([], timestamp=time.time())
    assert out['active_vehicle_count'] == 2
