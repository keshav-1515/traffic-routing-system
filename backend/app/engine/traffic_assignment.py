import networkx as nx
import numpy as np


class TrafficAssignment:

  @staticmethod
  def bpr_cost_function(
      t0: float,
      volume: float,
      capacity: float,
      alpha: float = 0.15,
      beta: float = 4.0,
  ) -> float:
    return t0 * (1.0 + alpha * (max(volume, 0) / max(capacity, 1.0)) ** beta)

  @staticmethod
  def frank_wolfe_step_size(
      current_flows: np.ndarray,
      auxiliary_flows: np.ndarray,
      iteration: int = 1,
  ) -> float:
    direction = auxiliary_flows - current_flows
    if np.all(direction == 0):
      return 0.0
    return float(2.0 / (iteration + 2.0))

  @classmethod
  def solve_user_equilibrium(
      cls,
      graph: nx.DiGraph,
      od_demands: list,
      max_iter: int = 10,
  ):
    """od_demands is a list of tuples: (origin_node, destination_node, demand_volume)"""
    edges = list(graph.edges(keys=True if graph.is_multigraph() else False))
    edge_idx = {edge: i for i, edge in enumerate(edges)}
    flows = np.zeros(len(edges))

    for it in range(1, max_iter + 1):
      # Update travel times based on current flow
      for edge in edges:
        u, v = edge[0], edge[1]
        data = (
            graph[u][v][edge[2]]
            if graph.is_multigraph()
            else graph[u][v]
        )
        t0 = data.get("free_flow_time", 10.0)
        cap = data.get("capacity", 50.0)
        idx = edge_idx[edge]
        data["travel_time"] = cls.bpr_cost_function(t0, flows[idx], cap)

      # All-or-nothing auxiliary assignment
      aux_flows = np.zeros(len(edges))
      for orig, dest, demand in od_demands:
        try:
          path = nx.shortest_path(graph, orig, dest, weight="travel_time")
          for i in range(len(path) - 1):
            u, v = path[i], path[i + 1]
            # Match edge
            for e in edges:
              if e[0] == u and e[1] == v:
                aux_flows[edge_idx[e]] += demand
                break
        except (nx.NetworkXNoPath, nx.NodeNotFound):
          continue

      # Frank-Wolfe convex combination step
      gamma = cls.frank_wolfe_step_size(flows, aux_flows, iteration=it)
      flows = flows + gamma * (aux_flows - flows)

    return {edge: float(flows[edge_idx[edge]]) for edge in edges}