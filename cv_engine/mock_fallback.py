"""Deterministic mock stream for offline/demo mode."""
import time
from .schemas import Metrics


class MockStream:
    def __init__(self):
        self._t0 = int(time.time())
        self._step = 0

    def next(self):
        self._step += 1
        ts = time.time()
        # simple deterministic pattern: vary counts with step
        cars = 5 + (self._step % 5)
        motos = 1 + ((self._step // 2) % 3)
        buses = 0 if (self._step % 7) else 1
        trucks = 0 if (self._step % 9) else 1
        m = Metrics(timestamp=ts, total_vehicles=cars + motos + buses + trucks,
                    cars=cars, motorcycles=motos, buses=buses, trucks=trucks,
                    lane_counts={})
        return m
