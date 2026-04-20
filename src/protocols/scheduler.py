import random
import uuid
import math
from typing import Tuple, List
from config.config_handler import ConfigHandler

class BeaconScheduler:
    def __init__(self):
        cfg = ConfigHandler()
        
        self.static_interval: float = cfg.get('scheduler', 'static_interval')
        self.min_interval: float = cfg.get('scheduler', 'beacon_min_interval')
        self.max_interval: float = cfg.get('scheduler', 'beacon_max_interval')
        self.scheduler_type: str = None
        self.default_velocity: float = cfg.get('buoys', 'default_velocity')

        self.last_static_send_time: float = -random.uniform(0, self.static_interval)
        self.last_dynamic_send_time: float = -random.uniform(0, self.min_interval)

        self.next_static_interval: float = self.static_interval
        self.next_dynamic_interval: float = None

    def get_next_check_interval(self) -> float:
        match self.scheduler_type:
            case "static":
                return self.static_interval
            case "dynamic_adab" | "dynamic_acab":
                return self.next_dynamic_interval if self.next_dynamic_interval is not None else self.min_interval
            case _:
                raise ValueError(f"Unknown scheduler type: {self.scheduler_type}")

    def should_send(self, 
            battery: float, 
            velocity: Tuple[float, float], 
            neighbors: List[Tuple[uuid.UUID, float, Tuple[float, float]]], 
            current_time: float
        ) -> bool:

        match self.scheduler_type:
            case "static":
                return self.should_send_static(current_time)
            case "dynamic_adab" | "dynamic_acab":
                return self.should_send_dynamic(battery, velocity, neighbors, current_time)
            case _:
                raise ValueError(f"Unknown scheduler type: {self.scheduler_type}")

    # For static scheduling checks if the time since the last send exceeds the static interval
    def should_send_static(self, current_time: float) -> bool:
        time_since_last = current_time - self.last_static_send_time
        
        if time_since_last >= self.next_static_interval:
            self.last_static_send_time = current_time
            return True
        return False

    def should_send_dynamic(
        self,
        battery: float,
        velocity: Tuple[float, float],
        neighbors: List[Tuple[uuid.UUID, float, Tuple[float, float]]],
        current_time: float,
    ) -> bool:
        
        if not self.next_dynamic_interval:
            self.next_dynamic_interval = self.compute_interval(velocity, neighbors, current_time)
        
        time_since_last = current_time - self.last_dynamic_send_time
        
        if time_since_last >= self.next_dynamic_interval:
            self.last_dynamic_send_time = current_time
            self.next_dynamic_interval = self.compute_interval(velocity, neighbors, current_time)
            return True
        return False

    def compute_interval(
        self,
        velocity: Tuple[float, float],
        neighbors: List[Tuple[uuid.UUID, float, Tuple[float, float]]],
        current_time: float,
    ) -> float:
        
        match self.scheduler_type:
            case "dynamic_acab":
                n_neighbors = len(neighbors)
                NEIGHBORS_THRESHOLD = 10
                density_score = min(1.0, n_neighbors / NEIGHBORS_THRESHOLD)

                CONTACT_THRESHOLD = 20.0
                if neighbors:
                    last_contact = max((ts for _, ts, _ in neighbors), default=current_time)
                    delta = current_time - last_contact
                    contact_score = max(0.0, 1.0 - (delta / CONTACT_THRESHOLD))
                else:
                    contact_score = 0.0

                vx, vy = velocity
                speed = math.hypot(vx, vy)
                mobility_score = min(1.0, speed / (self.default_velocity if self.default_velocity > 0 else 0.001))

                w_density = 0.4
                w_contact = 0.3
                w_mobility = 0.3

                combined = (w_density * density_score + 
                        w_contact * contact_score + 
                        w_mobility * (1.0 - mobility_score))
                
            case "dynamic_adab":
                n_neighbors = len(neighbors)
                NEIGHBORS_THRESHOLD = 15
                density_score = min(1.0, n_neighbors / NEIGHBORS_THRESHOLD)
                combined = density_score
                
            case _:
                raise ValueError(f"Unknown scheduler type: {self.scheduler_type}")

        fq = combined * combined
        bi_min = self.min_interval
        bi = bi_min + fq * (self.max_interval - bi_min)

        jitter_amount = (self.max_interval - self.min_interval) * 0.1 

        max_positive_jitter = min(jitter_amount, self.max_interval - bi)
        max_negative_jitter = min(jitter_amount, bi - self.min_interval)

        return bi + random.uniform(-max_negative_jitter, max_positive_jitter)