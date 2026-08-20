"""Deterministic mock stream for offline/demo mode."""
import time
from .schemas import Metrics


class MockStream:
    def __init__(self):
        self._t0 = int(time.time())
        self._step = 0

    def next(self, traffic=None):
        self._step += 1
        ts = time.time()
        if traffic and traffic.get('total_vehicles', 0) > 0:
            total = int(traffic['total_vehicles'])
            if total < 4:
                cars, motos, buses, trucks = total, 0, 0, 0
            else:
                cars = max(1, round(total * 0.68))
                motos = max(1, round(total * 0.20))
                buses = max(0, round(total * 0.06))
                trucks = max(0, total - cars - motos - buses)
            average_speed = float(traffic.get('average_speed_kmh', 0.0) or 0.0)
            active_tracked = int(traffic.get('active_tracked', total) or total)
        else:
            # Small deterministic pattern for an idle/offline demonstration.
            cars = 5 + (self._step % 5)
            motos = 1 + ((self._step // 2) % 3)
            buses = 0 if (self._step % 7) else 1
            trucks = 0 if (self._step % 9) else 1
            average_speed = 21.0 + (self._step % 3)
            active_tracked = cars + motos + buses + trucks
        m = Metrics(timestamp=ts, total_vehicles=cars + motos + buses + trucks,
                    cars=cars, motorcycles=motos, buses=buses, trucks=trucks,
                    active_vehicle_count=active_tracked,
                    average_speed_kmh=round(average_speed, 2),
                    min_speed_kmh=max(5.0, round(average_speed - 8.0, 2)),
                    max_speed_kmh=round(average_speed + 8.0, 2),
                    lane_counts={})
        return m
