from collections import deque
import numpy as np


class MapMatcher:

  def __init__(self, graph=None):
    self.graph = graph
    self.segment_buffers = {}

  def find_nearest_edge(self, lat: float, lng: float):
    """Snaps a GPS point to the nearest road network node/edge."""
    if not self.graph:
      return None

    best_node = None
    min_dist = float("inf")

    for node, data in self.graph.nodes(data=True):
      node_lat = data.get("y", data.get("lat", 0.0))
      node_lng = data.get("x", data.get("lng", 0.0))
      dist = (lat - node_lat) ** 2 + (lng - node_lng) ** 2
      if dist < min_dist:
        min_dist = dist
        best_node = node

    return best_node

  def add_telemetry_point(
      self, edge: tuple, speed: float, window_size: int = 20
  ):
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
        "flow": round(flow, 2),
    }