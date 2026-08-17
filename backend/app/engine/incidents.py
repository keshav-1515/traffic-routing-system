import networkx as nx

class IncidentManager:
    def __init__(self, graph: nx.MultiDiGraph):
        self.graph = graph
        self.incidents = {}

    def add_incident(self, u: int, v: int, key: int, is_closed: bool = True, clearance_delay_sec: float = 0.0):
        self.incidents[(u, v, key)] = {"is_closed": is_closed, "delay": clearance_delay_sec}
        if is_closed:
            self.graph[u][v][key]['travel_time'] = float('inf')
        else:
            self.graph[u][v][key]['travel_time'] = self.graph[u][v][key].get('travel_time', 10.0) + clearance_delay_sec

    def remove_incident(self, u: int, v: int, key: int, base_travel_time: float):
        if (u, v, key) in self.incidents:
            del self.incidents[(u, v, key)]
            self.graph[u][v][key]['travel_time'] = base_travel_time