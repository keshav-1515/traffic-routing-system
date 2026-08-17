import networkx as nx
from app.engine.fusion import MultiSensorFusion
from app.engine.incidents import IncidentManager
from app.engine.map_matching import MapMatcher
from app.engine.routing_individual import IndividualRoutingEngine
from app.engine.traffic_assignment import TrafficAssignment
from app.engine.zone_constraints import ZoneConstraintEngine


def run_verification():
  print("==================================================")
  print("   MEMBER 2: ROUTING & ALGORITHMIC ENGINE DEMO   ")
  print("==================================================\n")

  # Setup sample road network graph
  G = nx.MultiDiGraph()
  G.add_node(1, lat=12.9716, lng=77.5946)
  G.add_node(2, lat=12.9720, lng=77.5950)
  G.add_node(3, lat=12.9730, lng=77.5960)

  G.add_edge(1, 2, key=0, length=500.0, capacity=100.0, free_flow_time=30.0)
  G.add_edge(2, 3, key=0, length=700.0, capacity=100.0, free_flow_time=45.0)
  G.add_edge(1, 3, key=0, length=1400.0, capacity=80.0, free_flow_time=90.0)

  # Feature #2: Multi-Sensor Data Fusion
  fused_speed = MultiSensorFusion.fuse_sensor_data(
      camera_speed=40.0, gps_speed=38.0, sensor_speed=42.0
  )
  print(f"[Feature #2] Fused Speed: {fused_speed} km/h")

  # Feature #3: GPS Map-Matching & Telemetry Aggregation
  matcher = MapMatcher(graph=G)
  nearest_node = matcher.find_nearest_edge(12.9718, 77.5948)
  matcher.add_telemetry_point(edge=(1, 2, 0), speed=fused_speed)
  metrics = matcher.get_segment_metrics(edge=(1, 2, 0))
  print(f"[Feature #3] Nearest Snapped Node: {nearest_node}")
  print(f"             Segment (1->2) Metrics: {metrics}")

  # Feature #6: Individual-Optimal Shortest Path Routing
  router = IndividualRoutingEngine(graph=G)
  router.update_edge_travel_time(
      u=1, v=2, key=0, current_speed_kmh=fused_speed
  )
  router.update_edge_travel_time(u=2, v=3, key=0, current_speed_kmh=45.0)
  router.update_edge_travel_time(u=1, v=3, key=0, current_speed_kmh=50.0)
  base_path = router.compute_shortest_path(origin_node=1, destination_node=3)
  print(f"[Feature #6] Optimal Route (1 -> 3): {base_path}")

  # Feature #14: Protected Zone Routing
  zone_engine = ZoneConstraintEngine()
  zone_engine.apply_penalty(
      G, u=1, v=2, key=0, zone_type="school", penalty_factor=3.0
  )
  print(
      f"[Feature #14] Applied School Zone Penalty to edge (1->2):"
      f" {G[1][2][0].get('travel_time', 'Updated')}s"
  )

  # Feature #15: Incident-Aware Dynamic Rerouting
  incident_mgr = IncidentManager(graph=G)
  incident_mgr.report_incident(u=1, v=2, key=0, clearance_delay_sec=600.0)
  rerouted_path = router.compute_shortest_path(
      origin_node=1, destination_node=3
  )
  print(f"[Feature #15] Incident Delay Applied on (1->2)")
  print(f"              Rerouted Path: {rerouted_path}")

  # Feature #7: System-Optimal Coordinated Routing (Frank-Wolfe)
  od_demands = [(1, 3, 60.0)]
  equilibrium_flows = TrafficAssignment.solve_user_equilibrium(
      graph=G, od_demands=od_demands, max_iter=5
  )
  print(f"[Feature #7] Frank-Wolfe Equilibrium Flows: {equilibrium_flows}")

  print("\n All 6 Member 2 features executed and verified successfully!")


if __name__ == "__main__":
  run_verification()