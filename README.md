## Member 2 Contributions (Routing Engine & Algorithmic Traffic Lead)
- **Feature #2 (Multi-Sensor Data Fusion):** Implemented weighted confidence score model in `app/engine/fusion.py`.
- **Feature #3 (GPS Map-Matching & Telemetry):** Built spatial nearest-node snapping and segment speed/density/flow aggregation in `app/engine/map_matching.py`.
- **Feature #6 (Individual-Optimal Routing):** Implemented dynamic Dijkstra and A* routing on NetworkX directed graphs in `app/engine/routing_individual.py`.
- **Feature #7 (System-Optimal Traffic Assignment):** Implemented BPR cost functions and iterative Frank-Wolfe user equilibrium solver in `app/engine/traffic_assignment.py`.
- **Feature #14 (Protected Zone Routing):** Added sensitive zone penalty multipliers (schools, residential, hospitals) in `app/engine/zone_constraints.py`.
- **Feature #15 (Incident-Aware Dynamic Rerouting):** Implemented clearance-time delays and road exclusions in `app/engine/incidents.py`.
- **Verification & Testing:** Authored end-to-end integration test suite in `backend/test_engine.py`.