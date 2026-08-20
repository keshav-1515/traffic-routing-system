"""Simple centroid-based tracker with speed estimation and mock fallback."""
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Tuple, Optional
import time, math


@dataclass
class Track:
    track_id: int
    vehicle_class: str
    current_bbox: Tuple[float, float, float, float]
    center_x: float
    center_y: float
    first_seen: float
    last_seen: float
    trajectory: List[Tuple[float, float, float]] = field(default_factory=list)  # (ts, x, y)
    current_speed_kmh: float = 0.0
    average_speed_kmh: float = 0.0
    active: bool = True
    missed_frames: int = 0


class SimpleTracker:
    def __init__(self, max_missed=5, dedupe_distance_px=50, pixels_per_meter=10.0, fps=5, smoothing_alpha=0.4):
        self.tracks: Dict[int, Track] = {}
        self._next_id = 1
        self.max_missed = max_missed
        self.dedupe_distance = dedupe_distance_px
        self.pixels_per_meter = pixels_per_meter
        self.fps = fps
        self.smoothing_alpha = smoothing_alpha

    def _dist(self, x1, y1, x2, y2):
        return math.hypot(x1 - x2, y1 - y2)

    def update(self, detections: List, timestamp: Optional[float] = None):
        ts = timestamp if timestamp is not None else time.time()
        assigned = set()
        # match detections to existing tracks by nearest centroid
        for det in detections:
            best_tid = None
            best_dist = float('inf')
            cx, cy = det.center
            for tid, tr in self.tracks.items():
                if not tr.active:
                    continue
                d = self._dist(cx, cy, tr.center_x, tr.center_y)
                if d < best_dist and d <= self.dedupe_distance:
                    best_dist = d
                    best_tid = tid
            if best_tid is not None:
                tr = self.tracks[best_tid]
                tr.current_bbox = det.bbox
                tr.center_x, tr.center_y = cx, cy
                tr.last_seen = ts
                tr.trajectory.append((ts, cx, cy))
                tr.missed_frames = 0
                # speed estimate from last two points
                if len(tr.trajectory) >= 2:
                    t0, x0, y0 = tr.trajectory[-2]
                    dt = max(ts - t0, 1e-6)
                    dpix = self._dist(x0, y0, cx, cy)
                    dm = dpix / max(self.pixels_per_meter, 1e-6)
                    speed_ms = dm / dt
                    speed_kmh = speed_ms * 3.6
                    # exponential smoothing
                    tr.current_speed_kmh = (self.smoothing_alpha * speed_kmh
                                             + (1 - self.smoothing_alpha) * tr.current_speed_kmh)
                    # average speed over trajectory (simple mean of smoothed speeds)
                    # append to small buffer: compute average via historical speeds
                    total = sum([pt[3] for pt in getattr(tr, '_speeds', [])]) if hasattr(tr, '_speeds') else 0.0
                    speeds = getattr(tr, '_speeds', [])
                    speeds.append(tr.current_speed_kmh)
                    tr._speeds = speeds[-20:]
                    tr.average_speed_kmh = sum(tr._speeds) / len(tr._speeds)
                assigned.add(best_tid)
            else:
                # create new track
                tid = self._next_id
                self._next_id += 1
                t = Track(track_id=tid, vehicle_class=det.cls, current_bbox=det.bbox,
                          center_x=det.center[0], center_y=det.center[1], first_seen=ts, last_seen=ts,
                          trajectory=[(ts, det.center[0], det.center[1])])
                t._speeds = []
                self.tracks[tid] = t
                assigned.add(tid)

        # increment missed frames for unassigned tracks
        for tid, tr in list(self.tracks.items()):
            if tid not in assigned:
                tr.missed_frames += 1
                if tr.missed_frames > self.max_missed:
                    tr.active = False
        return self.metrics()

    def metrics(self):
        now = time.time()
        active = [t for t in self.tracks.values() if t.active]
        speeds = [t.current_speed_kmh for t in active if t.current_speed_kmh > 0]
        avg = sum(speeds) / len(speeds) if speeds else 0.0
        mn = min(speeds) if speeds else 0.0
        mx = max(speeds) if speeds else 0.0
        speed_by_class = {}
        for t in active:
            speed_by_class.setdefault(t.vehicle_class, []).append(t.current_speed_kmh)
        speed_by_class = {k: (sum(v) / len(v) if v else 0.0) for k, v in speed_by_class.items()}
        tracks_out = {str(t.track_id): {
            'vehicle_class': t.vehicle_class,
            'bbox': t.current_bbox,
            'center': (t.center_x, t.center_y),
            'first_seen': t.first_seen,
            'last_seen': t.last_seen,
            'current_speed_kmh': round(t.current_speed_kmh, 2),
            'average_speed_kmh': round(t.average_speed_kmh, 2),
            'active': t.active,
        } for t in self.tracks.values()}
        return {
            'timestamp': now,
            'active_vehicle_count': len(active),
            'average_speed_kmh': round(avg, 2),
            'min_speed_kmh': round(mn, 2),
            'max_speed_kmh': round(mx, 2),
            'speed_by_class': speed_by_class,
            'tracks': tracks_out,
        }


class MockTracker:
    def __init__(self):
        self._step = 0

    def update(self, detections, timestamp=None):
        self._step += 1
        now = time.time()
        # deterministic small set
        tracks = {
            '1': {'vehicle_class': 'car', 'bbox': (0,0,10,10), 'center': (10,10), 'first_seen': now-2, 'last_seen': now, 'current_speed_kmh': 25.0, 'average_speed_kmh': 24.5, 'active': True},
            '2': {'vehicle_class': 'bus', 'bbox': (20,20,40,40), 'center': (30,30), 'first_seen': now-5, 'last_seen': now, 'current_speed_kmh': 18.0, 'average_speed_kmh': 18.1, 'active': True},
        }
        return {
            'timestamp': now,
            'active_vehicle_count': len(tracks),
            'average_speed_kmh': 21.5,
            'min_speed_kmh': 18.0,
            'max_speed_kmh': 25.0,
            'speed_by_class': {'car': 25.0, 'bus': 18.0},
            'tracks': tracks,
        }
