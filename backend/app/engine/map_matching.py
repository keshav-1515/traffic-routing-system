from collections import deque
import numpy as np

class MapMatcher:
    def __init__(self, graph=None):
        self.graph = graph
        self.segment_buffers = {}

    def add_telemetry_point(self, edge: tuple, speed: float, window_size: int = 20):
        if edge not in self.segment_buffers:
            self.segment_buffers[edge] = deque(maxlen=window_size)
        self.segment_buffers[edge].append(speed)

    def get_segment_metrics(self, edge: tuple, segment_length_km: float = 1.0):
        speeds = self.segment_buffers.get(edge, [])
        if not speeds:
            return {"mean_speed": 0.0, "density": 0.0, "flow": 0.0}
        
        mean_speed = float(np.mean(speeds))
        density = len(speeds) / max(segment_length_km, 0.01)
        flow = density * mean_speed
        return {
            "mean_speed": round(mean_speed, 2),
            "density": round(density, 2),
            "flow": round(flow, 2)
        }