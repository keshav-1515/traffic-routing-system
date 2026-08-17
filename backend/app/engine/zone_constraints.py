import networkx as nx


class ZoneConstraintEngine:

  def __init__(self):
    self.penalties = {
        "school": 3.0,
        "residential": 2.0,
        "hospital": 2.5,
    }

  def apply_penalty(
      self,
      graph: nx.MultiDiGraph,
      u: int,
      v: int,
      key: int,
      zone_type: str = "school",
      penalty_factor: float = None,
  ):
    """Applies a multiplicative travel-time penalty to roads passing through sensitive zones."""
    factor = penalty_factor or self.penalties.get(zone_type, 1.5)

    if graph.has_edge(u, v, key):
      edge_data = graph[u][v][key]
      current_time = edge_data.get("travel_time", edge_data.get("length", 100.0) / 10.0)
      edge_data["travel_time"] = current_time * factor
      edge_data["zone_type"] = zone_type
      edge_data["penalty_factor"] = factor
      return edge_data["travel_time"]
    return None