"""
Traffic Engine — the "AI / simulation brain" of the dashboard
================================================================
This module owns everything the plan calls "core traffic AI logic":
dynamic congestion evolution, signal control (fixed / rule-based-adaptive /
Q-learning), incident injection, emergency green-waves, short-horizon
forecasting, emissions estimation, and a Frank-Wolfe static traffic
assignment engine for "what-if" counterfactual analysis.

It is deliberately dependency-free (stdlib + networkx only) so it drops
into virtual_world.py's Flask process and runs a live background thread.

It does NOT build or own the road graph — virtual_world.py does that.
This module receives the already-built `networkx.MultiDiGraph` and
mutates the same edge-attribute dictionaries virtual_world.py created
(`congestion_score`, `weight`, `current_volume`, ...), so every existing
API route (`/api/graph`, `/api/route`) automatically reflects the live
simulation with zero changes to those routes.

--------------------------------------------------------------------------
1. TRAFFIC FLOW MODEL — triangular fundamental diagram + link-based CTM
--------------------------------------------------------------------------
Each road edge is treated as one macroscopic "cell" (a standard, widely
used simplification of the Cell Transmission Model — Daganzo, 1994 — for
network-scale simulation where per-metre discretisation is unnecessary).

For a lane with free-flow speed vf and per-lane capacity qmax:
    critical density   kc = qmax / vf                  (veh/km/lane)
    jam density         kj = JAM_DENSITY_PER_LANE        (veh/km/lane)
    backward wave speed w  = qmax / (kj - kc)            (km/h)

    speed(k) = vf                          if k <= kc   (free-flow branch)
             = w * (kj - k) / k            if k >  kc   (congested branch)

This is the standard triangular flow-density relationship used in
production-grade CTM/LTM traffic simulators, and it is what gives the
simulation *realistic* behaviour: flow rises linearly with density up to
capacity, then speed collapses as density keeps climbing toward gridlock,
exactly like real congestion waves.

--------------------------------------------------------------------------
2. DEMAND — time-of-day Poisson trip generation + dynamic route choice
--------------------------------------------------------------------------
Trips are generated as a non-homogeneous Poisson process whose rate
follows a two-peak (AM/PM commute) diurnal curve. Each trip is assigned
the current shortest-time path (Dijkstra over the live, congestion-aware
`weight` attribute) — the same behaviour as a real-time navigation app,
which is what actually produces emergent congestion: vehicles pile onto
whichever route currently looks fastest.

--------------------------------------------------------------------------
3. SIGNALS — Webster's delay formula + 3 controller strategies
--------------------------------------------------------------------------
Every intersection with >= 3 approaches gets a 2-phase signal. Whichever
controller is active decides the green split; the resulting intersection
delay is computed with Webster's uniform-delay formula (Webster, 1958),
the textbook method traffic engineers use to convert cycle length / green
ratio / degree-of-saturation into an expected delay per vehicle. That
delay is added on top of the BPR link travel time, so a fixed-time signal
producing bad splits will visibly worsen congestion on the map, and an
adaptive controller correcting the split will visibly relieve it.

--------------------------------------------------------------------------
4. WHAT-IF ANALYSIS — Frank-Wolfe static User Equilibrium assignment
--------------------------------------------------------------------------
`run_frank_wolfe` is a standalone, independent traffic-assignment solver
(the algorithm the plan explicitly names). It is used for counterfactual
"before vs after" analysis (e.g. "what happens if I close this road?")
and does not touch the live simulation state.
"""

from __future__ import annotations

import math
import random
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import networkx as nx

EdgeKey = Tuple[object, object, object]  # (u, v, k) — matches nx MultiDiGraph edges

# ===========================================================================
# 1. FUNDAMENTAL DIAGRAM  (triangular flow-density relationship)
# ===========================================================================
JAM_DENSITY_PER_LANE = 160.0          # veh/km/lane at gridlock (typical urban value)
CAPACITY_PER_LANE_PER_HOUR = 1800.0   # must match virtual_world.py's constant

# ===========================================================================
# 1b. "DRIVE" ROUTING  (A* with an admissible heuristic + marginal-cost term)
# ===========================================================================
# This is the routing used by the single "drive from A to B" feature (as
# opposed to the background simulation's plain Dijkstra, which is fine for
# thousands of anonymous background trips but doesn't need to be the most
# sophisticated algorithm available).
#
# Algorithm choice: A* (Hart, Nilsson & Raphael, 1968) instead of plain
# Dijkstra. A* is the same algorithm family production routers (OSRM,
# GraphHopper, Valhalla) build on before their contraction-hierarchy
# pre-processing layer — with an admissible heuristic it explores far fewer
# nodes than Dijkstra while still guaranteeing the optimal path under the
# given edge-cost function. The heuristic used here is straight-line
# (great-circle) distance to the target divided by the fastest possible
# speed anywhere on the network — a true lower bound on remaining travel
# time, which is exactly what "admissible" requires for A* to stay optimal.
#
# Cost function choice: not raw distance, and not just this driver's own
# congestion-adjusted travel time (`weight`, already BPR + signal-delay
# aware) — an extra MARGINAL-COST externality term is added on top. This
# approximates System-Optimal routing (Beckmann et al., 1956): a link
# that's already heavily loaded gets penalised *more* than its own average
# travel time implies, so the router leans away from piling additional
# load onto already-congested links even when that link still looks like
# "your" fastest option — nudging one more trip toward the flow pattern a
# city-wide Frank-Wolfe System-Optimal assignment (see section 8 below)
# would prefer, without needing to re-solve the full network equilibrium
# for every single route request.
EARTH_RADIUS_KM = 6371.0009
MAX_NETWORK_SPEED_KMH = 100.0   # admissible upper bound used for the A* heuristic
SYSTEM_AWARE_GAMMA = 1.6        # externality strength
SYSTEM_AWARE_BETA = 2.0         # externality grows with the square of congestion
REROUTE_IMPROVEMENT_MARGIN_MIN = 0.25  # ignore reroutes worth less than this (avoid thrashing)
REROUTE_CHECK_INTERVAL_MIN = 0.5       # minimum sim-time between route re-checks (perf + stability)


def _haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance between two lat/lon points, in kilometres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def _make_astar_heuristic(G: nx.MultiDiGraph, target):
    """Admissible lower-bound heuristic (minutes) for nx.astar_path: great-circle
    distance to the target at the fastest speed the network could ever offer."""
    ty, tx = G.nodes[target]["y"], G.nodes[target]["x"]

    def h(n, _target=None):
        # networkx calls this as heuristic(node, target); target is already
        # closed over above, so the second positional arg is accepted but unused.
        ny, nx_ = G.nodes[n]["y"], G.nodes[n]["x"]
        dist_km = _haversine_km(ny, nx_, ty, tx)
        return (dist_km / MAX_NETWORK_SPEED_KMH) * 60.0

    return h


def _system_aware_edge_cost(data: Dict) -> float:
    """weight (own congestion-adjusted travel time) + a marginal-cost
    externality term that grows with how congested the link already is."""
    base = data.get("weight", data.get("free_flow_time_min", 0.5))
    congestion = max(data.get("congestion_score", 0.0), 0.0)
    externality = SYSTEM_AWARE_GAMMA * base * (congestion ** SYSTEM_AWARE_BETA)
    return base + externality


def _make_system_aware_weight():
    """
    Weight function for nx.astar_path/dijkstra on a MultiDiGraph. NetworkX
    calls a *callable* weight as weight(u, v, d) where `d` is the dict of
    ALL parallel edges between u and v (keyed by edge key) — not a single
    edge's attributes — so this must pick the cheapest parallel edge itself.
    """
    def weight_fn(u, v, d):
        return min(_system_aware_edge_cost(data) for data in d.values())
    return weight_fn


def _cheapest_parallel_key(G: nx.MultiDiGraph, u, v):
    parallel = G[u][v]
    return min(parallel, key=lambda kk: _system_aware_edge_cost(parallel[kk]))


def find_drive_route(G: nx.MultiDiGraph, source, target) -> Optional[List[EdgeKey]]:
    """
    Compute the best A-to-B route using A* with the admissible haversine
    heuristic and the marginal-cost-aware edge weight described above.
    Returns a list of (u, v, k) edges, or None if no route currently exists
    (e.g. the network is disconnected around a closure).
    """
    if source not in G or target not in G:
        return None
    if source == target:
        return []
    try:
        node_path = nx.astar_path(
            G, source, target,
            heuristic=_make_astar_heuristic(G, target),
            weight=_make_system_aware_weight(),
        )
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None
    return [(u, v, _cheapest_parallel_key(G, u, v)) for u, v in zip(node_path[:-1], node_path[1:])]


def fd_params(free_flow_speed_kmh: float) -> Tuple[float, float, float]:
    """Return (critical_density, jam_density, backward_wave_speed) for a lane."""
    vf = max(free_flow_speed_kmh, 5.0)
    kc = CAPACITY_PER_LANE_PER_HOUR / vf
    kj = JAM_DENSITY_PER_LANE
    if kj <= kc:
        kj = kc * 1.5
    w = CAPACITY_PER_LANE_PER_HOUR / (kj - kc)
    return kc, kj, w


def fd_speed(density_per_lane: float, vf_kmh: float, kc: float, kj: float, w: float) -> float:
    """Triangular fundamental diagram: speed(k). Density is veh/km/lane."""
    if density_per_lane <= 1e-6:
        return vf_kmh
    if density_per_lane <= kc:
        return vf_kmh
    k = min(density_per_lane, kj)
    q = max(w * (kj - k), 0.0)  # congested-branch flow, veh/hr/lane
    return q / k if k > 0 else 0.0


# ===========================================================================
# 2. EMISSIONS  (simplified speed-based CO2 curve — MOVES/COPERT-style shape)
# ===========================================================================
# Real emission-rate curves are U-shaped: idling/stop-start wastes fuel,
# and high aerodynamic drag wastes fuel again at high speed. The sweet
# spot sits around 55-65 km/h. Coefficients below reproduce that shape
# and land in the right order of magnitude for a typical passenger car
# (roughly 120-260 gCO2/km) without needing a full vehicle-emissions model.
def co2_g_per_km(speed_kmh: float) -> float:
    v = max(speed_kmh, 2.0)
    return max(1200.0 / v + 0.018 * v * v + 40.0, 60.0)


def idling_co2_g_per_min() -> float:
    """CO2 output of a stationary, idling engine (grams/minute)."""
    return 14.0


# ===========================================================================
# 3. SIGNAL CONTROL
# ===========================================================================
def webster_delay_seconds(cycle_s: float, green_ratio: float, degree_of_sat: float) -> float:
    """
    Webster's (1958) uniform-delay formula — the standard textbook estimate
    of average per-vehicle delay at a fixed-time signalised approach.

        d = C * (1 - lambda)^2 / (2 * (1 - lambda * x))

    C = cycle length (s), lambda = green ratio (g/C), x = degree of
    saturation (flow/capacity), clamped below 1 to keep the formula stable
    near/at oversaturation (a real intersection queues indefinitely beyond
    that point, which our link-level BPR term already penalises).
    """
    lam = min(max(green_ratio, 0.05), 0.95)
    x = min(max(degree_of_sat, 0.0), 0.97)
    denom = 2.0 * (1.0 - lam * x)
    if denom <= 1e-6:
        denom = 1e-6
    d = cycle_s * ((1.0 - lam) ** 2) / denom
    return max(d, 0.0)


@dataclass
class SignalController:
    """Base class: a 2-phase signal controlling a node's incoming edges.

    phase_a / phase_b are lists of incoming EdgeKeys grouped by approach
    direction (see `_group_phases`). `green_ratio_a` is the fraction of
    the cycle phase A gets (phase B gets the remainder, minus lost time).
    """
    node: object
    phase_a: List[EdgeKey]
    phase_b: List[EdgeKey]
    cycle_s: float = 90.0
    green_ratio_a: float = 0.5
    mode: str = "fixed"

    def green_ratio_for(self, edge: EdgeKey) -> float:
        if edge in self.phase_a:
            return self.green_ratio_a
        if edge in self.phase_b:
            return 1.0 - self.green_ratio_a
        return 0.5

    def update(self, dt_min: float, queue_a: float, queue_b: float) -> None:
        """Default (fixed-time) controller: never changes the split."""
        return


@dataclass
class AdaptiveSignal(SignalController):
    """
    Rule-based adaptive controller: every decision interval, re-splits
    green time proportionally to the relative queue pressure on each
    phase — the simplest real-world "actuated signal" strategy, and the
    baseline every adaptive-signal paper compares against.
    """
    min_green_ratio: float = 0.2
    max_green_ratio: float = 0.8

    def update(self, dt_min: float, queue_a: float, queue_b: float) -> None:
        total = queue_a + queue_b
        if total < 1e-6:
            target = 0.5
        else:
            target = queue_a / total
        target = min(max(target, self.min_green_ratio), self.max_green_ratio)
        # smooth toward the target so the signal doesn't visibly "teleport"
        self.green_ratio_a += (target - self.green_ratio_a) * min(1.0, dt_min / 2.0)


@dataclass
class QLearningSignal(SignalController):
    """
    Tabular Q-learning adaptive signal. State = (bucketed queue_a,
    bucketed queue_b). Action = {favor_a, favor_b, balanced}, which sets
    a target green ratio. Reward = -(queue_a + queue_b) after the action,
    i.e. the agent learns to pick splits that minimise total queueing —
    the same objective classic Q-learning traffic-signal papers use.

    This is intentionally a small, fast, *online* learner (a handful of
    states/actions) so it can run every tick inside a Flask background
    thread with no training data or GPU — it starts naive and visibly
    improves its splits over the course of a session.
    """
    alpha: float = 0.3
    gamma: float = 0.7
    epsilon: float = 0.15
    n_buckets: int = 5
    actions: Tuple[float, ...] = (0.3, 0.5, 0.7)
    q_table: Dict[Tuple[int, int, float], float] = field(default_factory=dict)
    _last_state: Optional[Tuple[int, int]] = None
    _last_action: Optional[float] = None

    def _bucket(self, q: float) -> int:
        return min(int(q // 3), self.n_buckets - 1)

    def _q(self, state, action) -> float:
        return self.q_table.get((state[0], state[1], action), 0.0)

    def update(self, dt_min: float, queue_a: float, queue_b: float) -> None:
        state = (self._bucket(queue_a), self._bucket(queue_b))
        reward = -(queue_a + queue_b)

        # learn from the previous step's transition (state, action) -> state
        if self._last_state is not None:
            best_next = max(self._q(state, a) for a in self.actions)
            old = self._q(self._last_state, self._last_action)
            td_target = reward + self.gamma * best_next
            new_q = old + self.alpha * (td_target - old)
            self.q_table[(self._last_state[0], self._last_state[1], self._last_action)] = new_q

        # epsilon-greedy action selection
        if random.random() < self.epsilon:
            action = random.choice(self.actions)
        else:
            action = max(self.actions, key=lambda a: self._q(state, a))

        self.green_ratio_a += (action - self.green_ratio_a) * min(1.0, dt_min / 1.5)
        self._last_state = state
        self._last_action = action


def _bearing_deg(G: nx.MultiDiGraph, u, v) -> float:
    """Approximate compass bearing of edge u->v from node lon/lat."""
    ux, uy = G.nodes[u].get("x", 0.0), G.nodes[u].get("y", 0.0)
    vx, vy = G.nodes[v].get("x", 0.0), G.nodes[v].get("y", 0.0)
    dx, dy = (vx - ux), (vy - uy)
    return math.degrees(math.atan2(dx, dy)) % 180.0


def build_signals(G: nx.MultiDiGraph, cycle_s: float = 90.0,
                   mode: str = "adaptive") -> Dict[object, SignalController]:
    """
    Create one signal controller per intersection with >= 3 incoming
    approaches. Incoming edges are split into two phase groups by
    real bearing (roughly N-S vs E-W traffic) when node coordinates are
    available, which is exactly how a real 2-phase signal is designed.
    """
    signals: Dict[object, SignalController] = {}
    cls = {"fixed": SignalController, "adaptive": AdaptiveSignal, "qlearning": QLearningSignal}.get(
        mode, AdaptiveSignal
    )

    for node in G.nodes:
        incoming = [(u, node, k) for u, _, k in G.in_edges(node, keys=True)]
        if len(incoming) < 3:
            continue
        phase_a, phase_b = [], []
        for (u, v, k) in incoming:
            bearing = _bearing_deg(G, u, v)
            (phase_a if bearing < 90.0 else phase_b).append((u, v, k))
        if not phase_a or not phase_b:
            # degenerate geometry (all edges same bearing) — split evenly by index
            phase_a, phase_b = incoming[0::2], incoming[1::2]
        signals[node] = cls(node=node, phase_a=phase_a, phase_b=phase_b, cycle_s=cycle_s, mode=mode)
    return signals


# ===========================================================================
# 4. INCIDENTS
# ===========================================================================
@dataclass
class EdgeOverride:
    """
    A single, unified per-edge manual/automatic control state. Replaces
    "incidents" as a list with one authoritative override per edge, which
    is what a real ops dashboard wants: click a road, see exactly what's
    currently being done to it, change it, done.

        closed           -- fully blocked (used by the "Block road" action
                             and the What-If road-closure API)
        capacity_factor   -- multiplies usable capacity: <1 = degraded
                             ("Traffic jam" / a stalled vehicle / a pothole),
                             >1 = boosted (the "Increase flow" action —
                             modelling a tidal-lane reversal, a temporary
                             extra lane, or hard signal priority)
        zone_type         -- None | "hospital" | "school" | "emergency".
                             hospital/school roads get a through-traffic
                             weight penalty (discourage cut-through
                             driving near sensitive sites); emergency
                             zones get a much larger penalty so ordinary
                             routing avoids them, while dispatched
                             emergency vehicles (green-wave) still use
                             their own explicit path regardless.
        expires_at_min    -- None = persistent until manually cleared;
                             otherwise auto-clears itself on the tick
                             after this simulated time.
        label             -- free-text reason, shown in the UI/API.
        manual_congestion_score
                          -- None = congestion is measured live from actual
                             flow (the normal path). When set (0..1), this
                             value is used verbatim instead of the
                             flow-derived measurement — this is what powers
                             both a manual "set congestion" admin slider and
                             the OpenCV traffic-camera analyzer (see
                             `TrafficSimulation.set_manual_congestion` /
                             virtual_world.py's `/api/traffic/video`), so an
                             operator or a camera feed can assert "this is
                             what's actually happening on this road" and
                             have it flow straight into routing weight,
                             overriding (but not replacing) the physics-based
                             simulation underneath it.
    """
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

# Extra multiplier stacked on top of ZONE_WEIGHT_MULTIPLIER while a
# city-wide "peak hour" policy is active (see `TrafficSimulation.set_peak_hour`)
# — during peak hour, routing should lean even harder on keeping through-
# traffic out of hospital/school/emergency zones than it does normally.
PEAK_HOUR_ZONE_EXTRA_MULTIPLIER = 3.0


# ===========================================================================
# 5. DEMAND GENERATION  (diurnal Poisson process)
# ===========================================================================
def demand_multiplier(minute_of_day: float) -> float:
    """Two-peak (AM/PM commute) demand curve, 0..~1.6, mirrors real urban counts."""
    def bump(center, width, height):
        return height * math.exp(-((minute_of_day - center) ** 2) / (2 * width * width))
    baseline = 0.12
    morning = bump(8 * 60, 65, 1.0)
    evening = bump(18 * 60, 80, 1.15)
    midday = bump(13 * 60, 130, 0.30)
    return baseline + morning + evening + midday


def _poisson_sample(lam: float) -> int:
    """Knuth's algorithm — pure-stdlib Poisson sampler (no numpy dependency)."""
    if lam <= 0:
        return 0
    if lam > 30:  # normal approximation is fine and much faster for big lambdas
        return max(0, int(round(random.gauss(lam, math.sqrt(lam)))))
    l = math.exp(-lam)
    k, p = 0, 1.0
    while True:
        k += 1
        p *= random.random()
        if p <= l:
            return k - 1


# ===========================================================================
# 6. VEHICLES
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

    @property
    def current_edge(self) -> EdgeKey:
        return self.route[self.edge_idx]


@dataclass
class DriveSession:
    """
    The single user-driven 'drive from A to B' trip (distinct from the
    anonymous background Vehicle population). Progresses through
    `path_edges` using the exact same enter/exit-time mechanics as a
    background Vehicle (see `_enter_edge`/`_advance_vehicles`), so its
    motion is governed by the same live physics — but it is re-evaluated
    against the live graph every tick so it can react to closures/jams
    that appear ahead of it (see `TrafficSimulation._advance_drive`).
    """
    source: object
    target: object
    path_edges: List[EdgeKey] = field(default_factory=list)
    edge_idx: int = 0
    enter_time_min: float = 0.0
    exit_time_min: float = 0.0
    status: str = "active"      # active | arrived | unreachable
    reroute_count: int = 0
    created_at_min: float = 0.0
    last_reroute_check_min: float = float("-inf")

    @property
    def current_edge(self) -> Optional[EdgeKey]:
        if 0 <= self.edge_idx < len(self.path_edges):
            return self.path_edges[self.edge_idx]
        return None


# ===========================================================================
# 7. THE SIMULATION ENGINE
# ===========================================================================
class TrafficSimulation:
    """
    Live, ticking macroscopic traffic simulation over a networkx graph
    already built & enriched by virtual_world.py.

    Call `.start()` to run it in a background thread inside the Flask
    process, or call `.tick()` yourself in a synchronous loop/test.
    """

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
        # Scale demand with network size so small synthetic grids and big
        # real-city extracts both produce sensible, non-degenerate load.
        # Calibrated so that, at rush-hour peak (demand_multiplier ~= 1.6),
        # popular corridors sit in the 0.5-1.0 congestion_score range
        # rather than staying near-empty — useful defaults for a live demo;
        # tune with `demand_scale` (also exposed over the API, wired to the
        # dashboard's "traffic density" slider) for a real deployment.
        self.base_spawn_rate_per_min = (
            base_spawn_rate_per_min if base_spawn_rate_per_min is not None
            else max(1.0, n_nodes * 15.0)
        )
        self.demand_scale = 1.0  # 0..~3, live-tunable multiplier on top of the above

        self.sim_clock_min = 8.0 * 60.0  # simulation "starts" at 08:00, peak of AM rush
        self.vehicles: Dict[int, Vehicle] = {}
        self._next_vid = 0
        self.edge_occupants: Dict[EdgeKey, set] = {e: set() for e in G.edges(keys=True)}
        # throughput counter (vehicles that *entered* each edge since the
        # last state update) — this, not instantaneous occupancy, is what
        # drives congestion_score. Occupancy snapshots break down whenever
        # a tick's sim-minutes exceed a short link's free-flow travel time
        # (the vehicle has already exited by the time we sample it); flow
        # is the standard, tick-size-independent traffic-engineering
        # measure (it's literally what capacity_veh_per_hr is denominated
        # in, and what the BPR formula was designed around).
        self.edge_entries_since_update: Dict[EdgeKey, int] = {e: 0 for e in G.edges(keys=True)}
        self.edge_overrides: Dict[EdgeKey, EdgeOverride] = {}
        self.signals: Dict[object, SignalController] = build_signals(G, mode=signal_mode)
        self.signal_mode = signal_mode

        # City-wide "peak hour" policy flag (distinct from `simulate_peak_surge`,
        # which is a temporary demand spike): while active, every hospital/
        # school/emergency-tagged road gets an EXTRA routing penalty on top
        # of its normal zone weight, so through-traffic avoids those roads
        # even harder during the window an admin flags as peak hour.
        self.peak_hour_active: bool = False

        # peak-hour surge: an optional temporary network-wide (or
        # corridor-targeted) demand multiplier — see simulate_peak_surge()
        self._surge_until_min: float = -1.0
        self._surge_intensity: float = 1.0
        self._surge_edge: Optional[EdgeKey] = None

        self.history: Dict[EdgeKey, deque] = {
            e: deque(maxlen=180) for e in G.edges(keys=True)
        }  # (sim_time_min, congestion_score) — 180 samples ≈ 3h at 1 sample/min

        self.trips_completed = 0
        self.total_travel_time_min = 0.0
        self.total_co2_g = 0.0

        self._green_wave: Dict[object, Tuple[EdgeKey, float]] = {}  # node -> (favored edge, expires_at_min)

        # The single active user-driven "drive from A to B" session, if any
        # (see DriveSession / start_drive / _advance_drive below).
        self.drive: Optional[DriveSession] = None

        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._running = False

        self._init_edge_fd_cache()

    # -- fundamental-diagram parameters are static per edge, cache them ----
    def _init_edge_fd_cache(self):
        self._fd_cache: Dict[EdgeKey, Tuple[float, float, float]] = {}
        for u, v, k, data in self.G.edges(keys=True, data=True):
            vf = data.get("free_flow_speed_kmh", 30.0)
            self._fd_cache[(u, v, k)] = fd_params(vf)

    # -----------------------------------------------------------------
    # Thread control
    # -----------------------------------------------------------------
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

    # -----------------------------------------------------------------
    # One simulation step
    # -----------------------------------------------------------------
    def tick(self):
        with self._lock:
            self.sim_clock_min += self.dt_min
            self._expire_overrides()
            self._spawn_vehicles()
            self._advance_vehicles()
            self._advance_drive()
            self._update_signals()
            self._update_edge_states()

    # ---- demand & routing -------------------------------------------------
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
            # During a targeted peak-hour surge, bias a share of the new
            # trips to actually route *through* the picked corridor (real
            # rush-hour surges concentrate on specific arterials, they
            # don't spread evenly across the whole city).
            if surging and self._surge_edge is not None and self.rng.random() < 0.6:
                su, sv, _ = self._surge_edge
                origin = self.rng.choice(nodes)
                dest = self.rng.choice(nodes)
                head = self._route_edges(origin, su)
                tail = self._route_edges(sv, dest)
                if head and tail:
                    full_route = head + [self._surge_edge] + tail
                    self._push_vehicle(full_route, is_emergency=False)
                    continue
            origin = self.rng.choice(nodes)
            dest = self.rng.choice(nodes)
            if origin == dest:
                continue
            route = self._route_edges(origin, dest)
            if not route:
                continue
            self._push_vehicle(route, is_emergency=False)

    def _route_edges(self, source, target) -> Optional[List[EdgeKey]]:
        try:
            path = nx.dijkstra_path(self.G, source, target, weight="weight")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None
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
        self.edge_occupants.setdefault(edge, set()).add(veh.vid)
        self.edge_entries_since_update[edge] = self.edge_entries_since_update.get(edge, 0) + 1

    # ---- vehicle progression ----------------------------------------------
    def _advance_vehicles(self):
        now = self.sim_clock_min
        done_ids = []
        for vid, veh in self.vehicles.items():
            guard = 0
            while veh.exit_time_min <= now and guard < 25:
                guard += 1
                self.edge_occupants.get(veh.current_edge, set()).discard(vid)
                veh.edge_idx += 1
                if veh.edge_idx >= len(veh.route):
                    self.trips_completed += 1
                    self.total_travel_time_min += now - veh.spawn_time_min
                    done_ids.append(vid)
                    break
                self._enter_edge(veh, veh.current_edge, veh.exit_time_min)
        for vid in done_ids:
            del self.vehicles[vid]

    # ---- signals ------------------------------------------------------
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

    # ---- manual/automatic edge overrides ---------------------------------
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
        """The 'Block road' action. Persistent until explicitly reopened."""
        with self._lock:
            if not closed and edge in self.edge_overrides:
                ov = self.edge_overrides[edge]
                ov.closed = False
                if ov.is_default():
                    del self.edge_overrides[edge]
                self._apply_override_now(edge)
                return ov
            ov = self._get_or_create_override(edge)
            ov.closed = closed
            ov.label = "manually closed" if closed else ov.label
            self._apply_override_now(edge)
            return ov

    def set_capacity_factor(self, edge: EdgeKey, factor: float,
                             duration_min: Optional[float] = None, label: str = "") -> EdgeOverride:
        """
        Degrade (factor < 1 — the 'Traffic jam' action / a stalled vehicle,
        pothole, illegal parking, etc.) or boost (factor > 1 — the
        'Increase flow' action: temporary extra capacity / signal
        priority / a tidal-lane reversal) an edge's usable capacity.
        `duration_min=None` means it persists until reset_edge() is called.
        """
        with self._lock:
            ov = self._get_or_create_override(edge)
            ov.capacity_factor = max(0.05, factor)
            ov.expires_at_min = (self.sim_clock_min + duration_min) if duration_min else None
            ov.label = label or ov.label
            self._apply_override_now(edge)
            return ov

    def set_manual_congestion(self, edge: EdgeKey, congestion_score: float,
                               duration_min: Optional[float] = None, label: str = "") -> EdgeOverride:
        """
        Force a specific congestion_score onto an edge instead of letting it
        be measured from live flow — the mechanism behind both a manual
        "set congestion" admin control and camera-derived congestion
        (OpenCV motion-density analysis of an uploaded traffic clip; see
        virtual_world.py's `/api/traffic/video`). `duration_min=None` means
        the override persists until reset_edge()/clear is called; a camera
        reading is typically applied with a short duration so a stale
        snapshot doesn't dictate routing forever.
        """
        with self._lock:
            ov = self._get_or_create_override(edge)
            ov.manual_congestion_score = max(0.0, min(1.0, congestion_score))
            ov.expires_at_min = (self.sim_clock_min + duration_min) if duration_min else ov.expires_at_min
            ov.label = label or ov.label
            self._apply_override_now(edge)
            return ov

    def clear_manual_congestion(self, edge: EdgeKey) -> None:
        """Return an edge to live flow-measured congestion (keeps any other override, e.g. a zone tag)."""
        with self._lock:
            ov = self.edge_overrides.get(edge)
            if ov is None:
                return
            ov.manual_congestion_score = None
            if ov.is_default():
                del self.edge_overrides[edge]
            self._apply_override_now(edge)

    def set_peak_hour(self, enabled: bool) -> bool:
        """
        City-wide policy toggle: while on, hospital/school/emergency zone
        roads get PEAK_HOUR_ZONE_EXTRA_MULTIPLIER stacked on top of their
        normal zone penalty, discouraging cut-through traffic during the
        window an admin flags as peak hour. Immediately re-scores every
        edge so the effect is visible without waiting for the next tick.
        """
        with self._lock:
            self.peak_hour_active = bool(enabled)
            for edge in list(self.G.edges(keys=True)):
                self._apply_override_now(edge)
            return self.peak_hour_active

    def reset_scenario(self) -> None:
        """
        City-wide 'reset' action: clears every closure, capacity jam/boost,
        and manual/camera congestion override, and turns peak hour off —
        but KEEPS zone tags (hospital/school/emergency labeling is treated
        as durable city data, not a transient scenario tweak). Live
        flow-measured congestion simply resumes on the next tick.
        """
        with self._lock:
            self.peak_hour_active = False
            for edge, ov in list(self.edge_overrides.items()):
                ov.closed = False
                ov.capacity_factor = 1.0
                ov.manual_congestion_score = None
                ov.expires_at_min = None
                if ov.is_default():
                    del self.edge_overrides[edge]
            for edge in list(self.G.edges(keys=True)):
                self._apply_override_now(edge)

    def set_zone(self, edge: EdgeKey, zone_type: Optional[str]) -> Optional[EdgeOverride]:
        """Tag/untag a road as a hospital / school / emergency zone."""
        with self._lock:
            if zone_type is None:
                if edge in self.edge_overrides:
                    ov = self.edge_overrides[edge]
                    ov.zone_type = None
                    if ov.is_default():
                        del self.edge_overrides[edge]
                        self._apply_override_now(edge)
                        return None
                    self._apply_override_now(edge)
                    return ov
                return None
            ov = self._get_or_create_override(edge)
            ov.zone_type = zone_type
            self._apply_override_now(edge)
            return ov

    def reset_edge(self, edge: EdgeKey):
        """Clear every manual override on a segment (the 'Reset segment' action)."""
        with self._lock:
            self.edge_overrides.pop(edge, None)
            self._apply_override_now(edge)

    def simulate_peak_surge(self, edge: Optional[EdgeKey] = None,
                             duration_min: float = 30.0, intensity: float = 2.6):
        """
        The 'Peak-hour surge' action: automatically runs a self-contained
        rush-hour simulation for `duration_min` — no further manual input
        needed. Network-wide trip generation is multiplied by `intensity`,
        and if an edge was selected, a majority of the extra demand is
        specifically routed through it (real surges concentrate on a
        corridor, not spread uniformly), so the effect is visible right
        where the planner clicked.
        """
        with self._lock:
            self._surge_until_min = self.sim_clock_min + duration_min
            self._surge_intensity = intensity
            self._surge_edge = edge

    def set_demand_scale(self, scale: float):
        """Live control for overall trip-generation intensity — this is what
        the dashboard's 'Traffic density' slider should drive."""
        with self._lock:
            self.demand_scale = max(0.0, min(scale, 4.0))

    def _capacity_factor(self, edge: EdgeKey) -> float:
        ov = self.edge_overrides.get(edge)
        return ov.capacity_factor if ov else 1.0

    # ---- emergency green wave -----------------------------------------
    def emergency_green_wave(self, path_nodes: List[object], vehicle_speed_kmh: float = 60.0,
                              hold_s: float = 25.0):
        """
        Schedule green-wave priority along a corridor: for every node the
        emergency vehicle will pass, compute its ETA from cumulative edge
        lengths / vehicle_speed_kmh and hold that node's approach green
        around the ETA — the standard transit-signal-priority technique.
        """
        with self._lock:
            cum_km = 0.0
            now = self.sim_clock_min
            for i in range(len(path_nodes) - 1):
                u, v = path_nodes[i], path_nodes[i + 1]
                if v not in self.G[u]:
                    continue
                k = min(self.G[u][v], key=lambda kk: self.G[u][v][kk].get("length", 1e9))
                length_km = self.G[u][v][k].get("length", 50.0) / 1000.0
                cum_km += length_km
                eta_min = now + (cum_km / max(vehicle_speed_kmh, 5.0)) * 60.0
                if v in self.signals:
                    sig = self.signals[v]
                    edge = (u, v, k)
                    favor = 0.95 if edge in sig.phase_a else 0.05
                    sig.green_ratio_a = favor if edge in sig.phase_a else (1.0 - favor)
                    self._green_wave[v] = (edge, eta_min + hold_s / 60.0)
            veh = self._push_vehicle(
                self._route_edges(path_nodes[0], path_nodes[-1]) or [], is_emergency=True
            ) if len(path_nodes) >= 2 else None
            return veh

    # ---- "drive from A to B" session --------------------------------------
    def _path_weight(self, edges: List[EdgeKey]) -> float:
        """Sum of live routing `weight` (minutes) across a list of edges.
        A missing/now-invalid edge is treated as very expensive rather than
        raising, so a stale cached path degrades gracefully into "reroute
        immediately" instead of crashing the sim loop."""
        total = 0.0
        for (u, v, k) in edges:
            if v in self.G[u] and k in self.G[u][v]:
                total += self.G[u][v][k].get("weight", 1e6)
            else:
                total += 1e6
        return total

    def start_drive(self, source, target) -> Dict:
        """
        Start (or replace) the single active drive session: compute the
        best A-to-B route with `find_drive_route` (A* + marginal-cost
        weighting — see section 1b) and begin progressing it using the
        same tick-based enter/exit-time mechanics as background vehicles.
        """
        with self._lock:
            edges = find_drive_route(self.G, source, target)
            if edges is None:
                self.drive = None
                return {"active": False, "status": "unreachable"}
            self.drive = DriveSession(source=source, target=target, path_edges=edges,
                                       created_at_min=self.sim_clock_min)
            if not edges:  # source == target
                self.drive.status = "arrived"
                return self._drive_state_locked()
            self._drive_enter_edge(edges[0], self.sim_clock_min)
            return self._drive_state_locked()

    def stop_drive(self) -> None:
        with self._lock:
            self.drive = None

    def _drive_enter_edge(self, edge: EdgeKey, now: float) -> None:
        u, v, k = edge
        data = self.G[u][v][k]
        travel_time_min = max(data.get("weight", data.get("free_flow_time_min", 0.5)), 0.05)
        self.drive.enter_time_min = now
        self.drive.exit_time_min = now + travel_time_min

    def _advance_drive(self) -> None:
        """Called once per tick (already inside self._lock via tick()).
        Progresses the active drive session and re-checks the route ahead
        of it against the live graph every tick, so a road block/jam that
        appears anywhere downstream is reacted to immediately — without
        interrupting whichever edge is currently being driven (the same
        behaviour a real turn-by-turn navigation app has: it recalculates
        the rest of the trip, it doesn't teleport you off your current
        road)."""
        d = self.drive
        if d is None or d.status != "active":
            return
        now = self.sim_clock_min

        guard = 0
        while d.exit_time_min <= now and guard < 25:
            guard += 1
            d.edge_idx += 1
            if d.edge_idx >= len(d.path_edges):
                d.status = "arrived"
                return
            self._drive_enter_edge(d.path_edges[d.edge_idx], d.exit_time_min)

        current_edge = d.current_edge
        if current_edge is None:
            d.status = "arrived"
            return
        _, v, _ = current_edge

        # Re-check the route ahead only when we just moved onto a new edge,
        # or enough sim-time has passed since the last check — not on every
        # single tick. This avoids A*-ing every tick (real cost on a large
        # real-city graph) and avoids visibly flip-flopping the highlighted
        # route between two near-identical-cost paths as background traffic
        # causes tiny weight fluctuations.
        just_changed_edge = guard > 0
        due_for_check = (now - d.last_reroute_check_min) >= REROUTE_CHECK_INTERVAL_MIN
        if not (just_changed_edge or due_for_check):
            return
        d.last_reroute_check_min = now

        remaining_after_current = d.path_edges[d.edge_idx + 1:]
        remaining_weight = self._path_weight(remaining_after_current)

        alt_edges = find_drive_route(self.G, v, d.target)
        if alt_edges is None:
            # Only give up if the destination is genuinely unreachable from
            # here right now; a transient closure that still leaves *some*
            # path should just make the alternative more expensive, not None.
            if not remaining_after_current:
                d.status = "unreachable"
            return

        alt_weight = self._path_weight(alt_edges)
        if alt_weight < remaining_weight - REROUTE_IMPROVEMENT_MARGIN_MIN:
            d.path_edges = d.path_edges[:d.edge_idx + 1] + alt_edges
            d.reroute_count += 1

    def get_drive_state(self) -> Dict:
        with self._lock:
            return self._drive_state_locked()

    def _drive_state_locked(self) -> Dict:
        d = self.drive
        if d is None:
            return {"active": False}
        if d.status == "unreachable":
            return {"active": False, "status": "unreachable", "reroute_count": d.reroute_count}
        if d.status == "arrived" or d.current_edge is None:
            return {"active": False, "status": "arrived", "reroute_count": d.reroute_count}

        u, v, k = d.current_edge
        edata = self.G[u][v][k]
        u_node, v_node = self.G.nodes[u], self.G.nodes[v]
        remaining_edges = d.path_edges[d.edge_idx:]

        geometry = [[u_node["x"], u_node["y"]]]
        for (_a, b, _k) in remaining_edges:
            bn = self.G.nodes[b]
            geometry.append([bn["x"], bn["y"]])

        elapsed_on_current = max(self.sim_clock_min - d.enter_time_min, 0.0)
        eta_min = max(self._path_weight(remaining_edges) - elapsed_on_current, 0.0)
        distance_remaining_m = sum(
            self.G[a][b][kk].get("length", 0.0) for (a, b, kk) in remaining_edges
        )

        return {
            "active": True,
            "status": "active",
            "source": str(d.source),
            "target": str(d.target),
            "sim_clock_min": round(self.sim_clock_min, 3),
            "tick_seconds": self.tick_seconds,
            "sim_minutes_per_tick": self.dt_min,
            "current_edge": {
                "u": str(u), "v": str(v), "k": str(k),
                "u_coords": [u_node["x"], u_node["y"]],
                "v_coords": [v_node["x"], v_node["y"]],
                "enter_time_min": round(d.enter_time_min, 3),
                "exit_time_min": round(d.exit_time_min, 3),
                "congestion_score": edata.get("congestion_score"),
                "current_speed_kmh": edata.get("current_speed_kmh"),
            },
            "route_geometry": {"type": "LineString", "coordinates": geometry},
            "eta_min": round(eta_min, 2),
            "distance_remaining_m": round(distance_remaining_m, 1),
            "reroute_count": d.reroute_count,
        }

    # ---- live vehicle positions (for map animation) ----------------------
    def _apply_override_now(self, edge: EdgeKey):
        """
        Re-derive an edge's weight/congestion immediately after a manual
        override changes (block/jam/boost/zone/reset), instead of waiting
        for the next tick — so a route query or reroute preview run right
        after clicking a button reflects the change instantly. Reuses the
        last-measured flow (flow_veh_per_hr) against the *new* capacity to
        get an immediate, still-physically-grounded congestion estimate;
        the next regular tick will refine it with freshly measured flow.
        """
        u, v, k = edge
        if v not in self.G[u] or k not in self.G[u][v]:
            return
        data = self.G[u][v][k]
        base_capacity = max(data.get("capacity_veh_per_hr", 1800.0), 1e-3)
        override = self.edge_overrides.get(edge)
        cap_factor = max(override.capacity_factor if override else 1.0, 1e-3)
        effective_capacity = base_capacity * cap_factor
        flow = data.get("flow_veh_per_hr", 0.0)
        congestion_score = min(flow / effective_capacity, 1.4)

        manual_score = override.manual_congestion_score if override else None
        if manual_score is not None:
            congestion_score = manual_score
            data["current_speed_kmh"] = round(
                data.get("free_flow_speed_kmh", 30.0) * max(1.0 - 0.65 * congestion_score, 0.08), 1
            )
        data["congestion_score"] = round(congestion_score, 3)

        self._recompute_weight(data)
        sig = self.signals.get(v)
        if sig is not None:
            green_ratio = sig.green_ratio_for(edge)
            x = min(congestion_score * 1.2, 0.97)
            delay_s = webster_delay_seconds(sig.cycle_s, green_ratio, x)
            data["signal_delay_min"] = round(delay_s / 60.0, 3)
            data["weight"] = round(data["weight"] + data["signal_delay_min"], 4)

        is_closed = bool(override and override.closed)
        zone_type = override.zone_type if override else None
        if is_closed:
            data["weight"] = 1_000_000.0
            data["congestion_score"] = 1.0
            data["current_speed_kmh"] = 0.0
        elif cap_factor < 1.0:
            data["weight"] = round(data["weight"] * (1.0 + 3.0 * (1.0 - cap_factor)), 4)
        elif cap_factor > 1.0:
            data["weight"] = round(data["weight"] / cap_factor, 4)
        if zone_type and not is_closed:
            mult = ZONE_WEIGHT_MULTIPLIER.get(zone_type, 1.0)
            if self.peak_hour_active:
                mult *= PEAK_HOUR_ZONE_EXTRA_MULTIPLIER
            data["weight"] = round(data["weight"] * mult, 4)

        data["zone_type"] = zone_type
        data["override_label"] = override.label if override else ""
        data["incident"] = bool(override and (is_closed or cap_factor != 1.0 or manual_score is not None))

    def get_reroute_preview(self, edge: EdgeKey) -> Optional[Dict]:
        """
        'If I block/jam this road, what path does through-traffic take
        instead?' — computes the current cheapest path between the edge's
        two endpoints under live (post-override) weights and reports which
        edges it uses, so the frontend can highlight the actual detour on
        the map using the same geometry it already rendered for those
        roads. Returns `unchanged: True` when the fastest path still is
        the original edge (e.g. a mild jam that isn't actually worth
        detouring around).
        """
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
        """
        Returns each active vehicle's CURRENT EDGE and its enter/exit time
        window, not a single precomputed lat/lng. The client extrapolates
        position along that edge locally (the same technique the drive-car
        marker uses) instead of linearly interpolating between two raw
        poll snapshots - interpolating raw points is what let a vehicle
        that changed roads between polls draw a straight line cutting
        across open ground instead of following the turn. Extrapolating
        along the edge geometry means a vehicle is mathematically
        guaranteed to sit on a road at every animation frame.

        `sample_every` (the "1 marker per N vehicles" density control) is
        applied first, using `vid % sample_every == 0` rather than a fresh
        random draw each call. That keeps the *same* subset of vehicles
        visible from one poll to the next (as long as they're still on the
        road) instead of reshuffling which ones are shown every refresh,
        which is what made the dots look like they were skipping frames.
        Emergency vehicles are always included regardless of sampling, since
        those are rare and important to keep visible. `limit` remains as a
        hard safety cap for render performance after sampling is applied.
        """
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
            out.append({
                "id": veh.vid,
                "emergency": veh.is_emergency,
                "u_coords": [un["x"], un["y"]],
                "v_coords": [vn["x"], vn["y"]],
                "enter_time_min": round(veh.enter_time_min, 4),
                "exit_time_min": round(veh.exit_time_min, 4),
            })
        return {
            "vehicles": out,
            "sim_clock_min": round(sim_clock_min, 4),
            "tick_seconds": self.tick_seconds,
            "sim_minutes_per_tick": self.dt_min,
        }


    # ---- node (intersection) info ---------------------------------------
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
            "signal": ({
                "mode": sig.mode, "cycle_s": sig.cycle_s,
                "green_ratio_a": round(sig.green_ratio_a, 2),
            } if sig is not None else None),
        }

    # ---- state -> graph -------------------------------------------------
    def _update_edge_states(self):
        now = self.sim_clock_min
        total_co2_tick = 0.0
        dt_hours = max(self.dt_min, 1e-6) / 60.0

        for edge in self.G.edges(keys=True):
            u, v, k = edge
            data = self.G[u][v][k]
            occupants = self.edge_occupants.get(edge, set())
            volume = len(occupants)  # instantaneous count, kept for the map/UI
            lanes = max(data.get("lanes", 1), 1)
            length_km = max(data.get("length", 50.0) / 1000.0, 0.01)
            base_capacity = max(data.get("capacity_veh_per_hr", 1800.0), 1e-3)

            override = self.edge_overrides.get(edge)
            cap_factor = max(override.capacity_factor if override else 1.0, 1e-3)
            effective_capacity = base_capacity * cap_factor

            # Flow (throughput) is the tick-size-independent congestion
            # signal: how many vehicles actually used this link this tick,
            # expressed as an hourly rate, compared against how many the
            # link (and any incident on it) can actually carry.
            entries = self.edge_entries_since_update.get(edge, 0)
            flow_veh_per_hr = entries / dt_hours
            vc_ratio = flow_veh_per_hr / effective_capacity
            congestion_score = min(vc_ratio, 1.4)  # allow mild oversaturation signal
            self.edge_entries_since_update[edge] = 0

            # Density/speed still come from the triangular fundamental
            # diagram (driven by the flow we just measured) — this is what
            # actually feeds the CO2 estimate and the "current speed" the
            # digital twin displays, independent of tick granularity.
            kc, kj, w = self._fd_cache.get(edge, fd_params(data.get("free_flow_speed_kmh", 30.0)))
            equivalent_flow_per_lane = flow_veh_per_hr / lanes
            if equivalent_flow_per_lane <= kc * data.get("free_flow_speed_kmh", 30.0):
                speed_kmh = data.get("free_flow_speed_kmh", 30.0)
            else:
                # congested branch: solve q = w*(kj-k) => k, then v = q/k
                k_est = max(kj - equivalent_flow_per_lane / max(w, 1e-3), kc)
                speed_kmh = fd_speed(k_est, data.get("free_flow_speed_kmh", 30.0), kc, kj, w)
            speed_kmh *= max(min(cap_factor, 1.0), 0.02)  # a boost (>1) doesn't raise physical speed limits

            # A manual/camera-asserted congestion_score (admin slider or
            # OpenCV traffic-camera analysis — see set_manual_congestion)
            # overrides the flow-measured value for routing/display purposes,
            # same as a live jam/boost override does for capacity. Physical
            # flow is still measured underneath so it resumes seamlessly
            # once the override expires/clears.
            manual_score = override.manual_congestion_score if override else None
            if manual_score is not None:
                congestion_score = manual_score
                speed_kmh = data.get("free_flow_speed_kmh", 30.0) * max(1.0 - 0.65 * congestion_score, 0.08)

            # Not clamped to 1.0: values up to 1.4 represent real
            # oversaturation and are meant to feed extra BPR penalty into
            # recompute_weight below. The frontend's colour scale already
            # treats anything >= 0.7 as "red", so this is visually fine —
            # it just also lets true gridlock cost more travel time than
            # merely-at-capacity traffic, which flat-out clamping would hide.
            data["current_volume"] = volume
            data["flow_veh_per_hr"] = round(flow_veh_per_hr, 1)
            data["current_speed_kmh"] = round(speed_kmh, 1)
            data["congestion_score"] = round(congestion_score, 3)
            data["queue_estimate"] = round(volume * min(congestion_score, 1.0), 1)

            # signal delay added on top of the BPR link travel time
            self._recompute_weight(data)
            sig = self.signals.get(v)
            if sig is not None:
                green_ratio = sig.green_ratio_for(edge)
                x = min(congestion_score * 1.2, 0.97)
                delay_s = webster_delay_seconds(sig.cycle_s, green_ratio, x)
                data["signal_delay_min"] = round(delay_s / 60.0, 3)
                data["weight"] = round(data["weight"] + data["signal_delay_min"], 4)
            else:
                data["signal_delay_min"] = 0.0

            is_closed = bool(override and override.closed)
            zone_type = override.zone_type if override else None

            if is_closed:
                # A closure isn't "4x slower" — it's unroutable. Push the
                # weight far above anything Dijkstra would ever prefer
                # instead of relying on the BPR curve to express "blocked".
                data["weight"] = 1_000_000.0
                data["congestion_score"] = 1.0
                data["current_speed_kmh"] = 0.0
            elif cap_factor < 1.0:
                data["weight"] = round(data["weight"] * (1.0 + 3.0 * (1.0 - cap_factor)), 4)
            elif cap_factor > 1.0:
                # a capacity boost also directly relieves travel time
                data["weight"] = round(data["weight"] / cap_factor, 4)

            if zone_type and not is_closed:
                zone_mult = ZONE_WEIGHT_MULTIPLIER.get(zone_type, 1.0)
                if self.peak_hour_active:
                    zone_mult *= PEAK_HOUR_ZONE_EXTRA_MULTIPLIER
                data["weight"] = round(data["weight"] * zone_mult, 4)

            data["zone_type"] = zone_type
            data["override_label"] = override.label if override else ""
            data["incident"] = bool(override and (is_closed or cap_factor != 1.0 or manual_score is not None))

            emis_rate = co2_g_per_km(max(speed_kmh, 3.0))
            co2_g = emis_rate * (volume * length_km) if speed_kmh > 3.0 else volume * idling_co2_g_per_min() * self.dt_min
            total_co2_tick += co2_g

            self.history[edge].append((now, data["congestion_score"]))

        self.total_co2_g += total_co2_tick

    # -----------------------------------------------------------------
    # Forecasting — Holt's linear-trend exponential smoothing
    # -----------------------------------------------------------------
    def forecast(self, edge: EdgeKey, horizons_min=(15, 30, 60)) -> Dict[str, Dict]:
        """
        Lightweight statistical forecaster (Holt's double exponential
        smoothing) over each edge's recent congestion history. This is an
        intentionally cheap stand-in for the heavier XGBoost/STGCN model
        described in the product plan — same interface/output shape, so it
        can be swapped for a trained model later without touching the API.
        """
        hist = list(self.history.get(edge, []))
        if len(hist) < 4:
            current = self.G.edges[edge].get("congestion_score", 0.3)
            return {f"{h}min": {"congestion_score": current, "confidence": "low"} for h in horizons_min}

        alpha, beta = 0.5, 0.3
        level = hist[0][1]
        trend = hist[1][1] - hist[0][1]
        for _, val in hist[1:]:
            last_level = level
            level = alpha * val + (1 - alpha) * (level + trend)
            trend = beta * (level - last_level) + (1 - beta) * trend

        values = [v for _, v in hist]
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        confidence = "high" if variance < 0.01 else ("medium" if variance < 0.05 else "low")

        out = {}
        for h in horizons_min:
            steps = h / max(self.dt_min, 0.01)
            pred = level + trend * steps
            out[f"{h}min"] = {"congestion_score": round(min(max(pred, 0.0), 1.0), 3),
                               "confidence": confidence}
        return out

    # -----------------------------------------------------------------
    # Public read-only snapshot for the API layer
    # -----------------------------------------------------------------
    def get_state(self) -> Dict:
        with self._lock:
            edges = self.G.edges(keys=True, data=True)
            scores = [d.get("congestion_score", 0.0) for *_, d in edges]
            avg_load = (sum(scores) / len(scores)) if scores else 0.0
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
                "active_overrides": [
                    {"edge": list(map(str, e)), "closed": ov.closed,
                     "capacity_factor": ov.capacity_factor, "zone_type": ov.zone_type,
                     "manual_congestion_score": ov.manual_congestion_score,
                     "label": ov.label,
                     "clears_in_min": (round(ov.expires_at_min - self.sim_clock_min, 1)
                                       if ov.expires_at_min is not None else None)}
                    for e, ov in self.edge_overrides.items()
                ],
                "total_co2_kg": round(self.total_co2_g / 1000.0, 2),
                "signals": {
                    str(node): {"green_ratio_a": round(sig.green_ratio_a, 2), "mode": sig.mode}
                    for node, sig in self.signals.items()
                },
                "edges": {
                    f"{u}-{v}-{k}": {
                        "congestion_score": d.get("congestion_score"),
                        "weight": d.get("weight"),
                        "current_speed_kmh": d.get("current_speed_kmh"),
                        "current_volume": d.get("current_volume"),
                        "flow_veh_per_hr": d.get("flow_veh_per_hr"),
                        "queue_estimate": d.get("queue_estimate"),
                        "incident": d.get("incident", False),
                        "closed": bool(self.edge_overrides.get((u, v, k)) and self.edge_overrides[(u, v, k)].closed),
                        "capacity_factor": (self.edge_overrides[(u, v, k)].capacity_factor
                                             if (u, v, k) in self.edge_overrides else 1.0),
                        "zone_type": d.get("zone_type"),
                        "override_label": d.get("override_label", ""),
                        "signal_delay_min": d.get("signal_delay_min", 0.0),
                    }
                    for u, v, k, d in edges
                },
            }


# ===========================================================================
# 8. FRANK-WOLFE STATIC USER-EQUILIBRIUM ASSIGNMENT (what-if engine)
# ===========================================================================
def _bpr_time(free_flow_min: float, volume: float, capacity: float,
              alpha: float = 0.15, beta: float = 4.0) -> float:
    if capacity <= 0:
        return free_flow_min * 50.0  # effectively closed
    ratio = volume / capacity
    return free_flow_min * (1.0 + alpha * (ratio ** beta))


def _all_or_nothing(G: nx.MultiDiGraph, od_demand: Dict[Tuple, float],
                     times: Dict[EdgeKey, float]) -> Dict[EdgeKey, float]:
    """Assign every OD trip to its current shortest path (all-or-nothing)."""
    H = nx.DiGraph()
    for u, v, k in G.edges(keys=True):
        t = times.get((u, v, k), 1e6)
        if (u, v) not in H.edges or t < H[u][v]["weight"]:
            H.add_edge(u, v, weight=t, key=k)

    aux_flow: Dict[EdgeKey, float] = {e: 0.0 for e in G.edges(keys=True)}
    for (o, d), demand in od_demand.items():
        if demand <= 0 or o == d:
            continue
        try:
            path = nx.dijkstra_path(H, o, d, weight="weight")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            continue
        for u, v in zip(path[:-1], path[1:]):
            k = H[u][v]["key"]
            aux_flow[(u, v, k)] = aux_flow.get((u, v, k), 0.0) + demand
    return aux_flow


def run_frank_wolfe(G: nx.MultiDiGraph, od_demand: Dict[Tuple, float],
                     max_iter: int = 25, tol: float = 1e-3) -> Dict[EdgeKey, Dict]:
    """
    Classic Frank-Wolfe convex-combinations algorithm for static User
    Equilibrium traffic assignment (LeBlanc, Morlok & Pierskalla, 1975 —
    the standard method taught for this exact problem). Converges toward
    the flow pattern where no driver can unilaterally improve their travel
    time by switching routes (Wardrop's first principle).

    Returns per-edge {volume, travel_time_min, free_flow_time_min,
    capacity} at the converged (or best found) equilibrium.
    """
    edges = list(G.edges(keys=True))
    free_flow = {e: G.edges[e].get("free_flow_time_min", 1.0) for e in edges}
    capacity = {e: max(G.edges[e].get("capacity_veh_per_hr", 1800.0), 1e-3) for e in edges}

    volume: Dict[EdgeKey, float] = {e: 0.0 for e in edges}
    times = dict(free_flow)
    volume = _all_or_nothing(G, od_demand, times)

    for n in range(1, max_iter + 1):
        times = {e: _bpr_time(free_flow[e], volume[e], capacity[e]) for e in edges}
        aux = _all_or_nothing(G, od_demand, times)
        step = 2.0 / (n + 2.0)  # method-of-successive-averages step size (Frank-Wolfe standard)

        new_volume = {e: volume[e] + step * (aux.get(e, 0.0) - volume[e]) for e in edges}
        delta = sum(abs(new_volume[e] - volume[e]) for e in edges) / max(len(edges), 1)
        volume = new_volume
        if delta < tol:
            break

    times = {e: _bpr_time(free_flow[e], volume[e], capacity[e]) for e in edges}
    return {
        e: {
            "volume_veh_per_hr": round(volume[e], 1),
            "travel_time_min": round(times[e], 3),
            "free_flow_time_min": round(free_flow[e], 3),
            "capacity_veh_per_hr": round(capacity[e], 1),
        }
        for e in edges
    }


def estimate_od_demand(G: nx.MultiDiGraph, n_pairs: int = 60, total_trips_per_hr: float = 4000.0,
                        seed: Optional[int] = None) -> Dict[Tuple, float]:
    """
    Build a plausible OD demand matrix when the caller doesn't supply real
    survey/telemetry data: sample random (origin, destination) node pairs
    weighted toward higher-degree nodes (real intersections generate more
    trips than dead-ends), and split total network demand across them.
    """
    rng = random.Random(seed)
    nodes = list(G.nodes)
    if len(nodes) < 2:
        return {}
    weights = [max(G.degree(n), 1) for n in nodes]
    pairs = set()
    attempts = 0
    while len(pairs) < min(n_pairs, len(nodes) * (len(nodes) - 1)) and attempts < n_pairs * 20:
        attempts += 1
        o = rng.choices(nodes, weights=weights, k=1)[0]
        d = rng.choices(nodes, weights=weights, k=1)[0]
        if o != d:
            pairs.add((o, d))
    if not pairs:
        return {}
    per_pair = total_trips_per_hr / len(pairs)
    return {p: per_pair for p in pairs}


def compare_scenarios(G: nx.MultiDiGraph, od_demand: Optional[Dict[Tuple, float]] = None,
                       close_edges: Optional[List[EdgeKey]] = None,
                       capacity_multipliers: Optional[Dict[EdgeKey, float]] = None) -> Dict:
    """
    The "Counterfactual Impact Analysis" the plan asks for: run Frank-Wolfe
    once on the network as-is, once on a modified copy (closures / capacity
    changes applied), and report the before/after deltas so a planner can
    evaluate a change *before* enforcing it in the real world.
    """
    if od_demand is None:
        od_demand = estimate_od_demand(G)

    baseline = run_frank_wolfe(G, od_demand)

    G2 = G.copy()
    for e in (close_edges or []):
        if e in G2.edges:
            G2.edges[e]["capacity_veh_per_hr"] = 1e-3
            G2.edges[e]["free_flow_time_min"] *= 1000.0
    for e, mult in (capacity_multipliers or {}).items():
        if e in G2.edges:
            G2.edges[e]["capacity_veh_per_hr"] = max(G2.edges[e].get("capacity_veh_per_hr", 1800.0) * mult, 1e-3)

    scenario = run_frank_wolfe(G2, od_demand)

    def total_veh_min(result):
        return sum(r["volume_veh_per_hr"] * r["travel_time_min"] for r in result.values())

    baseline_total = total_veh_min(baseline)
    scenario_total = total_veh_min(scenario)

    worst_edges = sorted(
        scenario.keys(),
        key=lambda e: scenario[e]["travel_time_min"] - baseline.get(e, scenario[e])["travel_time_min"],
        reverse=True,
    )[:10]

    return {
        "baseline_total_veh_min": round(baseline_total, 1),
        "scenario_total_veh_min": round(scenario_total, 1),
        "network_delay_change_pct": round(
            100.0 * (scenario_total - baseline_total) / baseline_total, 2
        ) if baseline_total > 0 else 0.0,
        "most_affected_edges": [
            {
                "edge": [str(x) for x in e],
                "baseline_time_min": baseline.get(e, {}).get("travel_time_min"),
                "scenario_time_min": scenario[e]["travel_time_min"],
                "baseline_volume": baseline.get(e, {}).get("volume_veh_per_hr"),
                "scenario_volume": scenario[e]["volume_veh_per_hr"],
            }
            for e in worst_edges
        ],
    }
