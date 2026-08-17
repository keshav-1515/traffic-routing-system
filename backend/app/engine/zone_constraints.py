import networkx as nx

class ZoneConstraintManager:
    def __init__(self, graph: nx.MultiDiGraph):
        self.graph = graph

    def apply_soft_penalty(self, u: int, v: int, key: int, zone_type: str):
        multipliers = {"school": 3.0, "hospital": 2.5, "residential": 2.0}
        penalty = multipliers.get(zone_type, 1.5)
        current_time = self.graph[u][v][key].get('travel_time', 10.0)
        self.graph[u][v][key]['travel_time'] = current_time * penalty

    def apply_hard_constraint(self, u: int, v: int, key: int):
        self.graph[u][v][key]['travel_time'] = float('inf')