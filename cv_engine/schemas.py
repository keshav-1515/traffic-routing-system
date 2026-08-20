from dataclasses import dataclass, asdict
from typing import Dict


@dataclass
class Detection:
    cls: str
    confidence: float
    bbox: tuple  # (x1,y1,x2,y2)
    center: tuple  # (x,y)


@dataclass
class Metrics:
    timestamp: float = 0.0
    total_vehicles: int = 0
    cars: int = 0
    motorcycles: int = 0
    buses: int = 0
    trucks: int = 0
    lane_counts: Dict[str, int] = None
    # tracking / speed fields
    active_vehicle_count: int = 0
    average_speed_kmh: float = 0.0
    min_speed_kmh: float = 0.0
    max_speed_kmh: float = 0.0
    speed_by_class: Dict[str, float] = None
    tracks: Dict[str, Dict] = None

    def as_dict(self):
        d = asdict(self)
        if d.get('lane_counts') is None:
            d['lane_counts'] = {}
        if d.get('speed_by_class') is None:
            d['speed_by_class'] = {}
        if d.get('tracks') is None:
            d['tracks'] = {}
        return d
