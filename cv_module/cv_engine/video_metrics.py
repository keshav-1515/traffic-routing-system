from __future__ import annotations

from dataclasses import dataclass, field
from time import time
from typing import Any


@dataclass
class VideoMetrics:
    camera_id: str
    source: str
    mode: str = "video"
    timestamp: float = field(default_factory=time)
    cars: int = 0
    motorcycles: int = 0
    buses: int = 0
    trucks: int = 0
    total_vehicles: int = 0
    active_tracked: int = 0
    counted_vehicles: int = 0
    average_speed_kmh: float | None = None
    min_speed_kmh: float | None = None
    max_speed_kmh: float | None = None
    traffic_density: float = 0.0
    congestion_score: float = 0.0
    frame_number: int = 0
    processing_fps: float = 0.0
    inference_latency_ms: float = 0.0
    tracks: dict[str, dict[str, Any]] = field(default_factory=dict)
    counted_by_zone: dict[str, int] = field(default_factory=dict)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "camera_id": self.camera_id,
            "source": self.source,
            "mode": self.mode,
            "timestamp": self.timestamp,
            "cars": self.cars,
            "motorcycles": self.motorcycles,
            "buses": self.buses,
            "trucks": self.trucks,
            "total_vehicles": self.total_vehicles,
            "active_vehicle_count": self.active_tracked,
            "active_tracked": self.active_tracked,
            "counted_vehicles": self.counted_vehicles,
            "average_speed_kmh": self.average_speed_kmh,
            "min_speed_kmh": self.min_speed_kmh,
            "max_speed_kmh": self.max_speed_kmh,
            "traffic_density": self.traffic_density,
            "congestion_score": self.congestion_score,
            "frame_number": self.frame_number,
            "processing_fps": self.processing_fps,
            "inference_latency_ms": self.inference_latency_ms,
            "tracks": self.tracks,
            "counted_by_zone": self.counted_by_zone,
            "error": self.error,
        }
