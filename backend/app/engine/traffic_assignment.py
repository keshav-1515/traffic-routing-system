import numpy as np

class TrafficAssignment:
    @staticmethod
    def bpr_cost_function(t0: float, volume: float, capacity: float, alpha: float = 0.15, beta: float = 4.0) -> float:
        return t0 * (1.0 + alpha * (max(volume, 0) / max(capacity, 1.0)) ** beta)

    @staticmethod
    def frank_wolfe_step_size(current_flows: np.ndarray, auxiliary_flows: np.ndarray, iteration: int = 1) -> float:
        direction = auxiliary_flows - current_flows
        if np.all(direction == 0):
            return 0.0
        return float(2.0 / (iteration + 2.0))