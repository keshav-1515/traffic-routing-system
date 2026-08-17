from typing import Optional, Dict
from pydantic import BaseModel, Field

class SensorReading(BaseModel):
    camera_speed: Optional[float] = Field(None, description="Speed in km/h from camera CV model")
    gps_speed: Optional[float] = Field(None, description="Speed in km/h from aggregated GPS probes")
    sensor_speed: Optional[float] = Field(None, description="Speed in km/h from road loop/radar sensors")

class MultiSensorFusion:
    def __init__(self, default_weights: Dict[str, float] = None):
        self.weights = default_weights or {"camera": 0.4, "gps": 0.4, "sensor": 0.2}

    def fuse_speeds(self, reading: SensorReading) -> float:
        active_weights = 0.0
        weighted_sum = 0.0

        if reading.camera_speed is not None:
            w = self.weights["camera"]
            weighted_sum += w * reading.camera_speed
            active_weights += w

        if reading.gps_speed is not None:
            w = self.weights["gps"]
            weighted_sum += w * reading.gps_speed
            active_weights += w

        if reading.sensor_speed is not None:
            w = self.weights["sensor"]
            weighted_sum += w * reading.sensor_speed
            active_weights += w

        if active_weights == 0.0:
            raise ValueError("At least one sensor reading must be provided.")

        return round(weighted_sum / active_weights, 2)