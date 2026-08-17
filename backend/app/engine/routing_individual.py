import networkx as nx

class IndividualRoutingEngine:
    def __init__(self, graph: nx.MultiDiGraph):
        self.graph = graph

    def update_edge_travel_time(self, u: int, v: int, key: int, current_speed_kmh: float):
        length_meters = self.graph[u][v][key].get('length', 100.0)
        speed_mps = max(current_speed_kmh * (1000 / 3600), 1.0)
        travel_time_sec = length_meters / speed_mps
        self.graph[u][v][key]['travel_time'] = travel_time_sec

    def compute_shortest_path(self, origin_node: int, destination_node: int, algorithm: str = "astar"):
        if algorithm == "astar":
            return nx.astar_path(self.graph, origin_node, destination_node, weight='travel_time')
        return nx.dijkstra_path(self.graph, origin_node, destination_node, weight='travel_time')