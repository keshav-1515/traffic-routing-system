"""CV engine wrapper and manager for detection/counting."""
from .detector import YOLODetector, DummyDetector
from .counter import VehicleCounter
from .mock_fallback import MockStream
from .schemas import Metrics
from .tracker import SimpleTracker, MockTracker

import threading, time


class CVManager:
    def __init__(self, mode='auto', model_path=None, conf_thresh=0.35, sample_fps=5):
        self.mode = mode
        self.model_path = model_path
        self.conf_thresh = conf_thresh
        self.sample_fps = sample_fps
        self.detector = None
        self.counter = VehicleCounter()
        self.tracker = MockTracker() if self.mode == 'mock' else SimpleTracker()
        self.mock = MockStream()
        self.metrics = Metrics()
        self._stop = False
        self._thread = None
        self._traffic_source = None

        # Initialise the detector if possible; mock mode stays dependency-free.
        if self.mode == 'mock':
            self.detector = DummyDetector()
        else:
            try:
                self.detector = YOLODetector(model_path=model_path, conf_thresh=conf_thresh)
            except Exception:
                self.detector = DummyDetector()
                self.mode = 'mock'

    def set_mode(self, mode):
        if mode not in ('auto', 'mock'):
            raise ValueError('mode must be auto or mock')
        self.mode = mode
        if mode == 'mock':
            self.detector = DummyDetector()
            self.tracker = MockTracker()

    def set_traffic_source(self, source):
        self._traffic_source = source

    def start(self):
        if self._thread:
            return
        self._stop = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop = True
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

    def _loop(self):
        interval = 1.0 / max(1, self.sample_fps)
        while not self._stop:
            try:
                if self.mode == 'mock':
                    traffic = self._traffic_source() if self._traffic_source else None
                    self.metrics = self.mock.next(traffic)
                    time.sleep(interval)
                    continue
                frame = self._get_frame()
                dets = self.detector.detect(frame)
                counts_metrics = self.counter.update(dets)
                track_metrics = self.tracker.update(dets)
                # merge into a single schema
                m = Metrics()
                m.timestamp = track_metrics.get('timestamp', time.time())
                m.total_vehicles = counts_metrics.total_vehicles if hasattr(counts_metrics, 'total_vehicles') else counts_metrics.get('total_vehicles', 0)
                m.cars = counts_metrics.cars if hasattr(counts_metrics, 'cars') else counts_metrics.get('cars', 0)
                m.motorcycles = counts_metrics.motorcycles if hasattr(counts_metrics, 'motorcycles') else counts_metrics.get('motorcycles', 0)
                m.buses = counts_metrics.buses if hasattr(counts_metrics, 'buses') else counts_metrics.get('buses', 0)
                m.trucks = counts_metrics.trucks if hasattr(counts_metrics, 'trucks') else counts_metrics.get('trucks', 0)
                m.active_vehicle_count = track_metrics.get('active_vehicle_count', 0)
                m.average_speed_kmh = track_metrics.get('average_speed_kmh', 0.0)
                m.min_speed_kmh = track_metrics.get('min_speed_kmh', 0.0)
                m.max_speed_kmh = track_metrics.get('max_speed_kmh', 0.0)
                m.speed_by_class = track_metrics.get('speed_by_class', {})
                m.tracks = track_metrics.get('tracks', {})
                self.metrics = m
            except Exception:
                # on any error fall back to deterministic mock metric
                self.metrics = self.mock.next()
            time.sleep(interval)

    def _get_frame(self):
        # For now we don't capture real frames here; detector implementations
        # that require frames should override this manager or be called
        # directly by a camera capture loop. Return None for detector to
        # interpret (some detectors can fetch from camera themselves).
        return None

    def get_metrics(self):
        return self.metrics.as_dict()


# convenient singleton for the server to import
DEFAULT_CV = CVManager()
