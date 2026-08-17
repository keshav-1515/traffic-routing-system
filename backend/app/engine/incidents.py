import networkx as nx


class IncidentManager:
    def __init__(self, graph: nx.MultiDiGraph = None):
        self.graph = graph
        self.active_incidents = {}

    def report_incident(
        self,
        u: int,
        v: int,
        key: int = 0,
        clearance_delay_sec: float = 300.0,
        is_blocked: bool = False,
    ):
        edge = (u, v, key)
        self.active_incidents[edge] = {
            "delay": clearance_delay_sec,
            "blocked": is_blocked,
        }

        if self.graph and self.graph.has_edge(u, v, key):
            if is_blocked:
                self.graph[u][v][key]["travel_time"] = float("inf")
            else:
                current_time = self.graph[u][v][key].get("travel_time", 10.0)
                self.graph[u][v][key]["travel_time"] = current_time + clearance_delay_sec

    def add_incident(self, u: int, v: int, key: int = 0, clearance_delay_sec: float = 300.0, is_blocked: bool = False):
        self.report_incident(u, v, key, clearance_delay_sec, is_blocked)

    def remove_incident(self, u: int, v: int, key: int = 0):
        edge = (u, v, key)
        if edge in self.active_incidents:
            del self.active_incidents[edge]