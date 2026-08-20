"""Simple counting logic: centroid dedupe and per-class totals."""
from collections import deque, defaultdict
import time
from .schemas import Detection, Metrics


class VehicleCounter:
    def __init__(self, dedupe_distance_px=30, window_s=2.0):
        # recent centers for deduping: deque of (timestamp, class, x, y)
        self.recent = deque()
        self.dedupe_distance = dedupe_distance_px
        self.window_s = window_s
        self.totals = defaultdict(int)

    def update(self, detections):
        now = time.time()
        # expire old
        while self.recent and now - self.recent[0][0] > self.window_s:
            self.recent.popleft()

        counts = defaultdict(int)
        for d in detections:
            counted = False
            for ts, cls, x, y in list(self.recent):
                if cls != d.cls: continue
                dx = x - d.center[0]; dy = y - d.center[1]
                if (dx*dx + dy*dy) ** 0.5 <= self.dedupe_distance:
                    counted = True
                    break
            if not counted:
                counts[d.cls] += 1
                self.recent.append((now, d.cls, d.center[0], d.center[1]))

        # update totals (for this frame/window)
        # For our purposes, totals reflect instantaneous counts, not cumulative
        metrics = Metrics(timestamp=now)
        metrics.cars = counts.get('car', 0)
        metrics.motorcycles = counts.get('motorcycle', 0)
        metrics.buses = counts.get('bus', 0)
        metrics.trucks = counts.get('truck', 0)
        metrics.total_vehicles = sum([metrics.cars, metrics.motorcycles, metrics.buses, metrics.trucks])
        metrics.lane_counts = {}
        return metrics

    def metrics(self):
        # return zeroed metrics if nothing seen
        return Metrics(timestamp=time.time(), total_vehicles=0, cars=0, motorcycles=0, buses=0, trucks=0, lane_counts={})
