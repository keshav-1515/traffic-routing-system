"""
Traffic Engine — Complete working version with:
- Traffic signal control at every node
- Proper vehicle rerouting when jams occur
- No teleportation (vehicles move smoothly along edges)
"""

from __future__ import annotations

import math
import random
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Set, Any

import networkx as nx

EdgeKey = Tuple[object, object, object]

# ===========================================================================
# CONSTANTS
# ===========================================================================
JAM_DENSITY_PER_LANE = 160.0
CAPACITY_PER_LANE_PER_HOUR = 1800.0
EARTH_RADIUS_KM = 6371.0009
MAX_NETWORK_SPEED_KMH = 100.0
SYSTEM_AWARE_GAMMA = 1.6
SYSTEM_AWARE_BETA = 2.0
REROUTE_IMPROVEMENT_MARGIN_MIN = 0.05
REROUTE_CHECK_INTERVAL_MIN = 0.1
JAM_DETECTION_THRESHOLD = 0.75

# ===========================================================================
# ROUTING FUNCTIONS
# ===========================================================================

def _haversine_km(lat1, lon1, lat2, lon2):
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def _make_astar_heuristic(G: nx.MultiDiGraph, target):
    ty, tx = G.nodes[target]["y"], G.nodes[target]["x"]
    def h(n, _target=None):
        ny, nx_ = G.nodes[n]["y"], G.nodes[n]["x"]
        dist_km = _haversine_km(ny, nx_, ty, tx)
        return (dist_km / MAX_NETWORK_SPEED_KMH) * 60.0
    return h


def _system_aware_edge_cost(data: Dict) -> float:
    base = data.get("weight", data.get("free_flow_time_min", 0.5))
    if base >= 1_000_000.0:
        return 1_000_000.0
    congestion = max(data.get("congestion_score", 0.0), 0.0)
    externality = SYSTEM_AWARE_GAMMA * base * (congestion ** SYSTEM_AWARE_BETA)
    return base + externality


def _make_system_aware_weight():
    def weight_fn(u, v, d):
        return min(_system_aware_edge_cost(data) for data in d.values())
    return weight_fn


def _cheapest_parallel_key(G: nx.MultiDiGraph, u, v):
    parallel = G[u][v]
    return min(parallel, key=lambda kk: _system_aware_edge_cost(parallel[kk]))


def find_drive_route(G: nx.MultiDiGraph, source, target, avoid_nodes: Set[object] = None) -> Optional[List[EdgeKey]]:
    if source not in G or target not in G:
        return None
    if source == target:
        return []
    
    avoid_nodes = avoid_nodes or set()
    
    try:
        node_path = nx.astar_path(
            G, source, target,
            heuristic=_make_astar_heuristic(G, target),
            weight=_make_system_aware_weight(),
        )
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None
    
    if avoid_nodes:
        path_nodes = set(node_path)
        if path_nodes.intersection(avoid_nodes):
            try:
                G_copy = G.copy()
                for node in avoid_nodes:
                    if node in G_copy.nodes:
                        G_copy.remove_node(node)
                node_path = nx.astar_path(
                    G_copy, source, target,
                    heuristic=_make_astar_heuristic(G_copy, target),
                    weight=_make_system_aware_weight(),
                )
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                pass
    
    return [(u, v, _cheapest_parallel_key(G, u, v)) for u, v in zip(node_path[:-1], node_path[1:])]


# ===========================================================================
# SIGNAL CONTROL
# ===========================================================================

def webster_delay_seconds(cycle_s: float, green_ratio: float, degree_of_sat: float) -> float:
    lam = min(max(green_ratio, 0.05), 0.95)
    x = min(max(degree_of_sat, 0.0), 0.97)
    denom = 2.0 * (1.0 - lam * x)
    if denom <= 1e-6:
        denom = 1e-6
    d = cycle_s * ((1.0 - lam) ** 2) / denom
    return max(d, 0.0)


@dataclass
class SignalController:
    node: object
    phase_a: List[EdgeKey]
    phase_b: List[EdgeKey]
    cycle_s: float = 90.0
    green_ratio_a: float = 0.5
    mode: str = "fixed"
    current_phase: str = "A"
    time_in_phase: float = 0.0

    def green_ratio_for(self, edge: EdgeKey) -> float:
        if edge in self.phase_a:
            return self.green_ratio_a
        if edge in self.phase_b:
            return 1.0 - self.green_ratio_a
        return 0.5

    def get_current_signal_state(self) -> Dict:
        return {
            "node": str(self.node),
            "phase": self.current_phase,
            "green_ratio_a": round(self.green_ratio_a, 2),
            "cycle_seconds": self.cycle_s,
            "mode": self.mode
        }

    def update(self, dt_min: float, queue_a: float, queue_b: float) -> None:
        dt_s = dt_min * 60.0
        self.time_in_phase += dt_s
        
        # Switch phases when cycle time is up
        if self.time_in_phase >= self.cycle_s * self.green_ratio_a:
            if self.current_phase == "A":
                self.current_phase = "B"
            else:
                self.current_phase = "A"
            self.time_in_phase = 0.0


@dataclass
class AdaptiveSignal(SignalController):
    min_green_ratio: float = 0.2
    max_green_ratio: float = 0.8

    def update(self, dt_min: float, queue_a: float, queue_b: float) -> None:
        dt_s = dt_min * 60.0
        self.time_in_phase += dt_s
        
        # Adjust green ratio based on queue lengths
        total = queue_a + queue_b
        if total < 1e-6:
            target = 0.5
        else:
            target = queue_a / total
        target = min(max(target, self.min_green_ratio), self.max_green_ratio)
        self.green_ratio_a += (target - self.green_ratio_a) * min(1.0, dt_min / 2.0)
        
        # Switch phases
        if self.time_in_phase >= self.cycle_s * self.green_ratio_a:
            if self.current_phase == "A":
                self.current_phase = "B"
            else:
                self.current_phase = "A"
            self.time_in_phase = 0.0


def _bearing_deg(G: nx.MultiDiGraph, u, v) -> float:
    ux, uy = G.nodes[u].get("x", 0.0), G.nodes[u].get("y", 0.0)
    vx, vy = G.nodes[v].get("x", 0.0), G.nodes[v].get("y", 0.0)
    dx, dy = (vx - ux), (vy - uy)
    return math.degrees(math.atan2(dx, dy)) % 180.0


def build_signals(G: nx.MultiDiGraph, cycle_s: float = 90.0,
                  mode: str = "adaptive") -> Dict[object, SignalController]:
    signals: Dict[object, SignalController] = {}
    cls = {"fixed": SignalController, "adaptive": AdaptiveSignal}.get(mode, AdaptiveSignal)

    for node in G.nodes:
        incoming = [(u, node, k) for u, _, k in G.in_edges(node, keys=True)]
        if len(incoming) < 2:
            continue
        phase_a, phase_b = [], []
        for (u, v, k) in incoming:
            bearing = _bearing_deg(G, u, v)
            if bearing < 90.0:
                phase_a.append((u, v, k))
            else:
                phase_b.append((u, v, k))
        if not phase_a or not phase_b:
            phase_a, phase_b = incoming[0::2], incoming[1::2]
        signals[node] = cls(node=node, phase_a=phase_a, phase_b=phase_b, cycle_s=cycle_s, mode=mode)
    return signals


# ===========================================================================
# EMISSIONS
# ===========================================================================

def co2_g_per_km(speed_kmh: float) -> float:
    v = max(speed_kmh, 2.0)
    return max(1200.0 / v + 0.018 * v * v + 40.0, 60.0)


def idling_co2_g_per_min() -> float:
    return 14.0


# ===========================================================================
# DEMAND GENERATION
# ===========================================================================

def demand_multiplier(minute_of_day: float) -> float:
    def bump(center, width, height):
        return height * math.exp(-((minute_of_day - center) ** 2) / (2 * width * width))
    baseline = 0.12
    morning = bump(8 * 60, 65, 1.0)
    evening = bump(18 * 60, 80, 1.15)
    midday = bump(13 * 60, 130, 0.30)
    return baseline + morning + evening + midday


def _poisson_sample(lam: float) -> int:
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, int(round(random.gauss(lam, math.sqrt(lam)))))
    l = math.exp(-lam)
    k, p = 0, 1.0
    while True:
        k += 1
        p *= random.random()
        if p <= l:
            return k - 1


# ===========================================================================
# VEHICLES
# ===========================================================================

@dataclass
class Vehicle:
    vid: int
    route: List[EdgeKey]
    edge_idx: int = 0
    enter_time_min: float = 0.0
    exit_time_min: float = 0.0
    spawn_time_min: float = 0.0
    is_emergency: bool = False
    reroute_count: int = 0
    position: float = 0.0  # 0-1 progress along current edge

    @property
    def current_edge(self) -> EdgeKey:
        return self.route[self.edge_idx]


@dataclass
class EdgeOverride:
    edge: EdgeKey
    closed: bool = False
    capacity_factor: float = 1.0
    zone_type: Optional[str] = None
    expires_at_min: Optional[float] = None
    label: str = ""
    manual_congestion_score: Optional[float] = None

    def is_default(self) -> bool:
        return (not self.closed and abs(self.capacity_factor - 1.0) < 1e-6
                and self.zone_type is None and self.manual_congestion_score is None)


ZONE_WEIGHT_MULTIPLIER = {"hospital": 1.7, "school": 1.7, "emergency": 8.0}
PEAK_HOUR_ZONE_EXTRA_MULTIPLIER = 3.0


# ===========================================================================
# MAIN SIMULATION ENGINE
# ===========================================================================

class TrafficSimulation:
    def __init__(self, G: nx.MultiDiGraph, recompute_weight_fn,
                 tick_seconds: float = 2.0, sim_minutes_per_tick: float = 1.0,
                 signal_mode: str = "adaptive", max_vehicles: int = 3500,
                 base_spawn_rate_per_min: float = None, seed: Optional[int] = None):
        self.G = G
        self._recompute_weight = recompute_weight_fn
        self.tick_seconds = tick_seconds
        self.dt_min = sim_minutes_per_tick
        self.max_vehicles = max_vehicles
        self.rng = random.Random(seed)

        n_nodes = max(G.number_of_nodes(), 1)
        self.base_spawn_rate_per_min = (
            base_spawn_rate_per_min if base_spawn_rate_per_min is not None
            else max(1.0, n_nodes * 15.0)
        )
        self.demand_scale = 1.0

        self.sim_clock_min = 8.0 * 60.0
        self.vehicles: Dict[int, Vehicle] = {}
        self._next_vid = 0
        self.edge_occupants: Dict[EdgeKey, set] = {e: set() for e in G.edges(keys=True)}
        self.edge_entries_since_update: Dict[EdgeKey, int] = {e: 0 for e in G.edges(keys=True)}
        self.edge_overrides: Dict[EdgeKey, EdgeOverride] = {}
        self.signals: Dict[object, SignalController] = build_signals(G, mode=signal_mode)
        self.signal_mode = signal_mode

        self.peak_hour_active: bool = False
        self._surge_until_min: float = -1.0
        self._surge_intensity: float = 1.0
        self._surge_edge: Optional[EdgeKey] = None
        
        self.jammed_nodes: Set[object] = set()
        self.node_congestion_history: Dict[object, deque] = {node: deque(maxlen=10) for node in G.nodes}

        self.history: Dict[EdgeKey, deque] = {
            e: deque(maxlen=180) for e in G.edges(keys=True)
        }

        self.trips_completed = 0
        self.total_travel_time_min = 0.0
        self.total_co2_g = 0.0
        self._green_wave: Dict[object, Tuple[EdgeKey, float]] = {}

        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._running = False

        self._init_edge_fd_cache()

    def _init_edge_fd_cache(self):
        self._fd_cache: Dict[EdgeKey, Tuple[float, float, float]] = {}
        for u, v, k, data in self.G.edges(keys=True, data=True):
            vf = data.get("free_flow_speed_kmh", 30.0)
            kc = CAPACITY_PER_LANE_PER_HOUR / max(vf, 5.0)
            kj = JAM_DENSITY_PER_LANE
            if kj <= kc:
                kj = kc * 1.5
            w = CAPACITY_PER_LANE_PER_HOUR / (kj - kc) if (kj - kc) > 0 else 30.0
            self._fd_cache[(u, v, k)] = (kc, kj, w)

    def start(self):
        with self._lock:
            if self._running:
                return
            self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        with self._lock:
            self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def _loop(self):
        while self._running:
            self.tick()
            time.sleep(self.tick_seconds)

    def tick(self):
        with self._lock:
            self.sim_clock_min += self.dt_min
            self._expire_overrides()
            self._detect_jammed_nodes()
            self._spawn_vehicles()
            self._advance_vehicles()
            self._update_signals()
            self._update_edge_states()

    # ========================================================================
    # JAM DETECTION
    # ========================================================================

    def _detect_jammed_nodes(self):
        self.jammed_nodes.clear()
        for node in self.G.nodes:
            incoming_edges = [(u, node, k) for u, _, k in self.G.in_edges(node, keys=True)]
            if not incoming_edges:
                continue
            total_congestion = 0.0
            for edge in incoming_edges:
                data = self.G.edges[edge]
                total_congestion += data.get("congestion_score", 0.0)
            avg_congestion = total_congestion / len(incoming_edges)
            self.node_congestion_history[node].append(avg_congestion)
            if avg_congestion >= JAM_DETECTION_THRESHOLD:
                self.jammed_nodes.add(node)

    # ========================================================================
    # VEHICLE SPAWNING
    # ========================================================================

    def _spawn_vehicles(self):
        if len(self.vehicles) >= self.max_vehicles:
            return
        surging = self.sim_clock_min < self._surge_until_min
        intensity = self._surge_intensity if surging else 1.0
        rate = (self.base_spawn_rate_per_min * self.demand_scale * intensity
                * demand_multiplier(self.sim_clock_min % 1440.0))
        n_new = _poisson_sample(rate * self.dt_min)
        nodes = list(self.G.nodes)
        if len(nodes) < 2:
            return
        for _ in range(n_new):
            if len(self.vehicles) >= self.max_vehicles:
                break
            origin = self.rng.choice(nodes)
            dest = self.rng.choice(nodes)
            if origin == dest:
                continue
            route = self._route_edges(origin, dest)
            if not route:
                continue
            self._push_vehicle(route, is_emergency=False)

    def _route_edges(self, source, target, avoid_nodes: Set[object] = None) -> Optional[List[EdgeKey]]:
        avoid_nodes = avoid_nodes or set()
        try:
            path = nx.dijkstra_path(self.G, source, target, weight="weight")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None
        
        path_nodes = set(path)
        if avoid_nodes and path_nodes.intersection(avoid_nodes):
            try:
                G_copy = self.G.copy()
                for node in avoid_nodes:
                    if node in G_copy.nodes:
                        G_copy.remove_node(node)
                path = nx.dijkstra_path(G_copy, source, target, weight="weight")
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                pass
        
        edges = []
        for u, v in zip(path[:-1], path[1:]):
            parallel = self.G[u][v]
            k = min(parallel, key=lambda kk: parallel[kk].get("weight", 1e9))
            edges.append((u, v, k))
        return edges or None

    def _push_vehicle(self, route: List[EdgeKey], is_emergency: bool) -> Vehicle:
        vid = self._next_vid
        self._next_vid += 1
        veh = Vehicle(vid=vid, route=route, spawn_time_min=self.sim_clock_min,
                      is_emergency=is_emergency)
        self._enter_edge(veh, route[0], self.sim_clock_min)
        self.vehicles[vid] = veh
        return veh

    def _enter_edge(self, veh: Vehicle, edge: EdgeKey, now: float):
        u, v, k = edge
        data = self.G[u][v][k]
        travel_time_min = max(data.get("weight", data.get("free_flow_time_min", 0.5)), 0.05)
        veh.enter_time_min = now
        veh.exit_time_min = now + travel_time_min
        veh.position = 0.0
        self.edge_occupants.setdefault(edge, set()).add(veh.vid)
        self.edge_entries_since_update[edge] = self.edge_entries_since_update.get(edge, 0) + 1

    # ========================================================================
    # VEHICLE ADVANCEMENT WITH SMOOTH MOVEMENT (NO TELEPORTATION)
    # ========================================================================

    def _advance_vehicles(self):
        now = self.sim_clock_min
        done_ids = []
        
        for vid, veh in self.vehicles.items():
            # Check for reroute due to jams
            if not veh.is_emergency and self._should_reroute_vehicle(veh):
                self._reroute_vehicle(veh)
            
            # Calculate how much time has passed since last update
            time_since_enter = now - veh.enter_time_min
            edge_duration = veh.exit_time_min - veh.enter_time_min
            
            if edge_duration > 0:
                # Update position smoothly (0 to 1)
                veh.position = min(time_since_enter / edge_duration, 1.0)
            
            # Check if vehicle has reached the end of current edge
            if veh.position >= 1.0 or veh.exit_time_min <= now:
                # Move to next edge
                self.edge_occupants.get(veh.current_edge, set()).discard(vid)
                veh.edge_idx += 1
                veh.position = 0.0
                
                if veh.edge_idx >= len(veh.route):
                    # Vehicle arrived at destination
                    self.trips_completed += 1
                    self.total_travel_time_min += now - veh.spawn_time_min
                    done_ids.append(vid)
                else:
                    # Enter next edge
                    self._enter_edge(veh, veh.current_edge, now)
        
        # Remove completed vehicles
        for vid in done_ids:
            del self.vehicles[vid]

    def _should_reroute_vehicle(self, veh: Vehicle) -> bool:
        if veh.is_emergency:
            return False
        
        # Look ahead to detect jams
        look_ahead = 3
        for i in range(1, min(look_ahead + 1, len(veh.route) - veh.edge_idx)):
            edge_idx = veh.edge_idx + i
            if edge_idx >= len(veh.route):
                break
            u, v, k = veh.route[edge_idx]
            data = self.G[u][v][k]
            congestion = data.get("congestion_score", 0.0)
            if v in self.jammed_nodes or congestion >= JAM_DETECTION_THRESHOLD:
                return True
        return False

    def _reroute_vehicle(self, veh: Vehicle) -> bool:
        current_node = veh.route[veh.edge_idx][0]
        target = veh.route[-1][1]
        avoid_nodes = self.jammed_nodes.copy()
        
        try:
            new_route = self._route_edges(current_node, target, avoid_nodes=avoid_nodes)
        except Exception:
            return False
        
        if new_route and len(new_route) < len(veh.route[veh.edge_idx:]):
            veh.route = veh.route[:veh.edge_idx] + new_route
            veh.reroute_count += 1
            return True
        return False

    # ========================================================================
    # SIGNAL UPDATES
    # ========================================================================

    def _queue_estimate(self, edges: List[EdgeKey]) -> float:
        total = 0.0
        for e in edges:
            data = self.G.edges[e]
            total += data.get("current_volume", 0) * data.get("congestion_score", 0.0)
        return total

    def _update_signals(self):
        for node, sig in self.signals.items():
            qa = self._queue_estimate(sig.phase_a)
            qb = self._queue_estimate(sig.phase_b)
            sig.update(self.dt_min, qa, qb)

    # ========================================================================
    # EDGE STATE UPDATES
    # ========================================================================

    def _update_edge_states(self):
        now = self.sim_clock_min
        total_co2_tick = 0.0
        dt_hours = max(self.dt_min, 1e-6) / 60.0

        for edge in self.G.edges(keys=True):
            u, v, k = edge
            data = self.G[u][v][k]
            occupants = self.edge_occupants.get(edge, set())
            volume = len(occupants)
            lanes = max(data.get("lanes", 1), 1)
            length_km = max(data.get("length", 50.0) / 1000.0, 0.01)
            base_capacity = max(data.get("capacity_veh_per_hr", 1800.0), 1e-3)

            override = self.edge_overrides.get(edge)
            cap_factor = max(override.capacity_factor if override else 1.0, 1e-3)
            effective_capacity = base_capacity * cap_factor

            entries = self.edge_entries_since_update.get(edge, 0)
            flow_veh_per_hr = entries / dt_hours if dt_hours > 0 else 0
            vc_ratio = flow_veh_per_hr / effective_capacity if effective_capacity > 0 else 0
            congestion_score = min(vc_ratio, 1.4)
            self.edge_entries_since_update[edge] = 0

            manual_score = override.manual_congestion_score if override else None
            if manual_score is not None:
                congestion_score = manual_score

            speed_kmh = data.get("free_flow_speed_kmh", 30.0) * max(1.0 - 0.65 * congestion_score, 0.08)

            data["current_volume"] = volume
            data["flow_veh_per_hr"] = round(flow_veh_per_hr, 1)
            data["current_speed_kmh"] = round(speed_kmh, 1)
            data["congestion_score"] = round(congestion_score, 3)
            data["queue_estimate"] = round(volume * min(congestion_score, 1.0), 1)

            self._recompute_weight(data)
            
            # Apply signal delay
            sig = self.signals.get(v)
            if sig is not None:
                green_ratio = sig.green_ratio_for(edge)
                x = min(congestion_score * 1.2, 0.97)
                delay_s = webster_delay_seconds(sig.cycle_s, green_ratio, x)
                data["signal_delay_min"] = round(delay_s / 60.0, 3)
                data["weight"] = round(data["weight"] + data["signal_delay_min"], 4)
            else:
                data["signal_delay_min"] = 0.0

            # Apply overrides
            is_closed = bool(override and override.closed)
            zone_type = override.zone_type if override else None

            if is_closed:
                data["weight"] = 1_000_000.0
                data["congestion_score"] = 1.0
                data["current_speed_kmh"] = 0.0
            elif cap_factor < 1.0:
                data["weight"] = round(data["weight"] * (1.0 + 8.0 * (1.0 - cap_factor)), 4)
            elif cap_factor > 1.0:
                data["weight"] = round(data["weight"] / cap_factor, 4)

            if zone_type and not is_closed:
                zone_mult = ZONE_WEIGHT_MULTIPLIER.get(zone_type, 1.0)
                if self.peak_hour_active:
                    zone_mult *= PEAK_HOUR_ZONE_EXTRA_MULTIPLIER
                data["weight"] = round(data["weight"] * zone_mult, 4)

            data["zone_type"] = zone_type
            data["override_label"] = override.label if override else ""
            data["incident"] = bool(override and (is_closed or cap_factor != 1.0 or manual_score is not None))

            # CO2 emissions
            emis_rate = co2_g_per_km(max(speed_kmh, 3.0))
            if speed_kmh > 3.0:
                co2_g = emis_rate * (volume * length_km)
            else:
                co2_g = volume * idling_co2_g_per_min() * self.dt_min
            total_co2_tick += co2_g

            self.history[edge].append((now, data["congestion_score"]))

        self.total_co2_g += total_co2_tick

    # ========================================================================
    # OVERRIDE FUNCTIONS
    # ========================================================================

    def _get_or_create_override(self, edge: EdgeKey) -> EdgeOverride:
        ov = self.edge_overrides.get(edge)
        if ov is None:
            ov = EdgeOverride(edge=edge)
            self.edge_overrides[edge] = ov
        return ov

    def _expire_overrides(self):
        expired = [e for e, ov in self.edge_overrides.items()
                   if ov.expires_at_min is not None and ov.expires_at_min <= self.sim_clock_min]
        for e in expired:
            del self.edge_overrides[e]

    def set_road_closed(self, edge: EdgeKey, closed: bool) -> EdgeOverride:
        with self._lock:
            if not closed and edge in self.edge_overrides:
                ov = self.edge_overrides[edge]
                ov.closed = False
                if ov.is_default():
                    del self.edge_overrides[edge]
                return ov
            ov = self._get_or_create_override(edge)
            ov.closed = closed
            ov.label = "manually closed" if closed else ov.label
            return ov

    def set_capacity_factor(self, edge: EdgeKey, factor: float,
                             duration_min: Optional[float] = None, label: str = "") -> EdgeOverride:
        with self._lock:
            ov = self._get_or_create_override(edge)
            ov.capacity_factor = max(0.05, factor)
            ov.expires_at_min = (self.sim_clock_min + duration_min) if duration_min else None
            ov.label = label or ov.label
            return ov

    def set_manual_congestion(self, edge: EdgeKey, congestion_score: float,
                               duration_min: Optional[float] = None, label: str = "") -> EdgeOverride:
        with self._lock:
            ov = self._get_or_create_override(edge)
            ov.manual_congestion_score = max(0.0, min(1.0, congestion_score))
            ov.expires_at_min = (self.sim_clock_min + duration_min) if duration_min else ov.expires_at_min
            ov.label = label or ov.label
            return ov

    def reset_edge(self, edge: EdgeKey):
        with self._lock:
            self.edge_overrides.pop(edge, None)

    def set_zone(self, edge: EdgeKey, zone_type: Optional[str]) -> Optional[EdgeOverride]:
        with self._lock:
            if zone_type is None:
                if edge in self.edge_overrides:
                    ov = self.edge_overrides[edge]
                    ov.zone_type = None
                    if ov.is_default():
                        del self.edge_overrides[edge]
                    return None
                return None
            ov = self._get_or_create_override(edge)
            ov.zone_type = zone_type
            return ov

    def simulate_peak_surge(self, edge: Optional[EdgeKey] = None,
                             duration_min: float = 30.0, intensity: float = 2.6):
        with self._lock:
            self._surge_until_min = self.sim_clock_min + duration_min
            self._surge_intensity = intensity
            self._surge_edge = edge

    def set_demand_scale(self, scale: float):
        with self._lock:
            self.demand_scale = max(0.0, min(scale, 4.0))

    def reset_scenario(self) -> None:
        with self._lock:
            self.peak_hour_active = False
            self.edge_overrides.clear()

    # ========================================================================
    # GETTER FUNCTIONS
    # ========================================================================

    def get_reroute_preview(self, edge: EdgeKey) -> Optional[Dict]:
        u, v, k = edge
        with self._lock:
            try:
                path = nx.dijkstra_path(self.G, u, v, weight="weight")
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                return None
            total_weight = nx.dijkstra_path_length(self.G, u, v, weight="weight")
            path_edges = []
            total_length_m = 0.0
            for a, b in zip(path[:-1], path[1:]):
                parallel = self.G[a][b]
                ek = min(parallel, key=lambda kk: parallel[kk].get("weight", 1e9))
                path_edges.append([str(a), str(b), str(ek)])
                total_length_m += parallel[ek].get("length", 0.0)
            unchanged = (len(path) == 2 and path[0] == u and path[1] == v)
            return {
                "edges": path_edges,
                "time_min": round(total_weight, 2),
                "distance_m": round(total_length_m, 1),
                "unchanged": unchanged,
            }

    def get_vehicle_positions(self, limit: int = 400, sample_every: int = 1) -> Dict:
        with self._lock:
            vehicles = list(self.vehicles.values())
            sim_clock_min = self.sim_clock_min
        if sample_every > 1:
            vehicles = [v for v in vehicles if v.is_emergency or v.vid % sample_every == 0]
        if len(vehicles) > limit:
            vehicles = self.rng.sample(vehicles, limit)
        out = []
        for veh in vehicles:
            u, v, k = veh.current_edge
            if u not in self.G.nodes or v not in self.G.nodes:
                continue
            un, vn = self.G.nodes[u], self.G.nodes[v]
            # Interpolate position
            pos = veh.position
            lng = un["x"] + (vn["x"] - un["x"]) * pos
            lat = un["y"] + (vn["y"] - un["y"]) * pos
            out.append({
                "id": veh.vid,
                "emergency": veh.is_emergency,
                "lat": lat,
                "lng": lng,
                "enter_time_min": round(veh.enter_time_min, 4),
                "exit_time_min": round(veh.exit_time_min, 4),
                "position": round(pos, 3),
                "edge": [str(u), str(v), str(k)]
            })
        return {
            "vehicles": out,
            "sim_clock_min": round(sim_clock_min, 4),
            "tick_seconds": self.tick_seconds,
            "sim_minutes_per_tick": self.dt_min,
        }

    def get_node_info(self, node) -> Optional[Dict]:
        if node not in self.G.nodes:
            return None
        incoming = [(u, node, k) for u, _, k in self.G.in_edges(node, keys=True)]
        total_vehicles = sum(self.G.edges[e].get("current_volume", 0) for e in incoming)
        total_queue = sum(self.G.edges[e].get("queue_estimate", 0.0) for e in incoming)
        scores = [self.G.edges[e].get("congestion_score", 0.0) for e in incoming]
        avg_congestion = sum(scores) / len(scores) if scores else 0.0
        sig = self.signals.get(node)
        return {
            "node": str(node),
            "incoming_roads": len(incoming),
            "vehicles_nearby": total_vehicles,
            "queue_estimate": round(total_queue, 1),
            "avg_congestion": round(avg_congestion, 3),
            "signalized": sig is not None,
            "signal": (sig.get_current_signal_state() if sig is not None else None),
            "is_jammed": node in self.jammed_nodes,
        }

    def get_state(self) -> Dict:
        with self._lock:
            edges = self.G.edges(keys=True, data=True)
            scores = [d.get("congestion_score", 0.0) for *_, d in edges]
            avg_load = (sum(scores) / len(scores)) if scores else 0.0
            
            # Get signal states
            signal_states = {}
            for node, sig in self.signals.items():
                signal_states[str(node)] = sig.get_current_signal_state()
            
            return {
                "running": self._running,
                "sim_clock_min": round(self.sim_clock_min % 1440.0, 1),
                "sim_day_hhmm": f"{int(self.sim_clock_min % 1440 // 60):02d}:{int(self.sim_clock_min % 60):02d}",
                "signal_mode": self.signal_mode,
                "active_vehicles": len(self.vehicles),
                "trips_completed": self.trips_completed,
                "avg_travel_time_min": round(
                    self.total_travel_time_min / self.trips_completed, 2
                ) if self.trips_completed else None,
                "avg_network_load": round(avg_load, 3),
                "peak_surge_active": self.sim_clock_min < self._surge_until_min,
                "peak_hour_active": self.peak_hour_active,
                "jammed_nodes": [str(n) for n in self.jammed_nodes],
                "signals": signal_states,
                "total_co2_kg": round(self.total_co2_g / 1000.0, 2),
            }