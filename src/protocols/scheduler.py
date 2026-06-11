import random
import math
from config.config_handler import ConfigHandler

# Thesis (Corsi §3.4.5): BI_next = bi * (1 + ε), ε ∈ [-η, +η] with η = 0.05.
JITTER_FRACTION: float = 0.05

class BeaconScheduler:
    def __init__(
        self,
        scheduler_type: str,
        static_interval: float,
        min_interval: float,
        max_interval: float,
        default_velocity: float
    ):
        cfg = ConfigHandler()

        # Scheduler settings
        self.scheduler_type: str        = scheduler_type
        self.static_interval: float     = static_interval
        self.min_interval: float        = min_interval
        self.max_interval: float        = max_interval
        self.interval_range: float      = self.max_interval - self.min_interval
        self.default_velocity: float    = max(default_velocity, 0.001)
    
        # States for static/dynamic scheduling decisions
        self.last_fq: float                 = 0.0   # Last computed back-off intensity
        self.last_dynamic_send_time: float  = -random.uniform(0, self.min_interval)
        self.last_static_send_time: float   = -random.uniform(0, self.static_interval)
        self.next_dynamic_interval: float   = self.min_interval

        # Forwarding gate baseline (expected forwarders per cascade)
        self.forward_density_baseline: int = cfg.get('simulation', 'forward_density_baseline')

        # Scheduler-specific thresholds and weights (from config)
        self.acab_contact_threshold: float              = cfg.get('scheduler', 'acab_contact_threshold')
        self.acab_neighbors_threshold: float            = cfg.get('scheduler', 'acab_neighbors_threshold')
        self.adab_neighbors_threshold: float            = cfg.get('scheduler', 'adab_neighbors_threshold')
        self.acab_weights: tuple[float, float, float]   = tuple(cfg.get('scheduler', 'acab_weights'))

    def get_next_check_interval(self) -> float:
        match self.scheduler_type:
            case "static":
                return self.static_interval
            case "dynamic_adab" | "dynamic_acab":
                return self.next_dynamic_interval
            case _:
                raise ValueError(f"Unknown scheduler type: {self.scheduler_type}")

    def should_send(
            self,
            velocity: tuple[float, float],
            n_neighbors: int,
            last_contact_ts: float,
            current_time: float
    ) -> bool:

        match self.scheduler_type:
            case "static":
                return self.should_send_static(current_time)
            case "dynamic_adab" | "dynamic_acab":
                return self.should_send_dynamic(velocity, n_neighbors, last_contact_ts, current_time)
            case _:
                raise ValueError(f"Unknown scheduler type: {self.scheduler_type}")

    # For static scheduling checks if the time since the last send exceeds the static interval
    def should_send_static(self, current_time: float) -> bool:
        time_since_last = current_time - self.last_static_send_time

        if time_since_last >= self.static_interval:
            self.last_static_send_time = current_time
            return True
        return False

    def should_send_dynamic(
            self,
            velocity: tuple[float, float],
            n_neighbors: int,
            last_contact_ts: float,
            current_time: float,
    ) -> bool:
        time_since_last = current_time - self.last_dynamic_send_time

        if time_since_last >= self.next_dynamic_interval:
            self.last_dynamic_send_time = current_time
            self.next_dynamic_interval = self.compute_interval(velocity, n_neighbors, last_contact_ts, current_time)
            return True

        return False

    def compute_interval(
        self,
        velocity: tuple[float, float],
        n_neighbors: int,
        last_contact_ts: float,
        current_time: float,
    ) -> float:
        
        match self.scheduler_type:
            case "dynamic_acab":
                density_score = min(1.0, n_neighbors / self.acab_neighbors_threshold)

                if n_neighbors > 0 and last_contact_ts is not None:
                    delta = current_time - last_contact_ts
                    contact_score = max(0.0, 1.0 - (delta / self.acab_contact_threshold))
                else:
                    contact_score = 0.0

                vx, vy = velocity
                speed = math.hypot(vx, vy)
                mobility_score = min(1.0, speed / self.default_velocity)

                w_density, w_contact, w_mobility = self.acab_weights
                combined = (w_density * density_score + 
                            w_contact * contact_score + 
                            w_mobility * (1.0 - mobility_score))
            
            case "dynamic_adab":
                density_score = min(1.0, n_neighbors / self.adab_neighbors_threshold)
                combined = density_score
                
            case _:
                raise ValueError(f"Unknown scheduler type: {self.scheduler_type}")

        fq = combined * combined
        self.last_fq = fq
        bi = self.min_interval + fq * self.interval_range

        jittered = bi * (1.0 + random.uniform(-JITTER_FRACTION, JITTER_FRACTION))
        return max(self.min_interval, min(self.max_interval, jittered))

    # Forwarding gate decision based on neighbor density and scheduler type
    def should_forward(self, n_neighbors: int) -> bool:
        if self.forward_density_baseline <= 0 or n_neighbors <= self.forward_density_baseline:
            return True

        # P = forward_density_baseline / n_neighbors, 
        # with a reduction factor for dynamic schedulers based on last back-off intensity
        p = self.forward_density_baseline / n_neighbors
        if self.scheduler_type != "static":
            p *= max(0.3, 1.0 - self.last_fq)
        return random.random() < p