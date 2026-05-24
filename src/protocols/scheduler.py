import random
import math
from config.config_handler import ConfigHandler

class BeaconScheduler:
    def __init__(self):
        cfg = ConfigHandler()
        
        self.static_interval: float = cfg.get('scheduler', 'static_interval')
        self.min_interval: float = cfg.get('scheduler', 'beacon_min_interval')
        self.max_interval: float = cfg.get('scheduler', 'beacon_max_interval')
        self.scheduler_type: str = None
        default_velocity = cfg.get('buoys', 'default_velocity')
        self.velocity_divisor: float = max(default_velocity, 0.001)  # Avoid div-by-zero checks later

        self.last_static_send_time: float = -random.uniform(0, self.static_interval)
        self.last_dynamic_send_time: float = -random.uniform(0, self.min_interval)

        self.next_static_interval: float = self.static_interval
        self.next_dynamic_interval: float = None

        # Last computed back-off intensity (fq = combined²). Used so forwarding can
        # throttle itself in step with how aggressively the scheduler has backed off.
        self.last_fq: float = 0.0

        # Forwarding gate: target this many forwarders per cascade in dense
        # neighborhoods. 0 disables the gate (every receiver forwards).
        self.forward_density_baseline: int = cfg.get('simulation', 'forward_density_baseline')
        
        # Cache interval range and jitter scale
        self.interval_range: float = self.max_interval - self.min_interval
        self.jitter_scale: float = self.interval_range * 0.1
        
        # Cache scheduler-specific thresholds and weights
        self.acab_neighbors_threshold: float = 10.0
        self.acab_contact_threshold: float = 20.0
        self.acab_weights: tuple[float, float, float] = (0.4, 0.3, 0.3)  # density, contact, mobility
        self.adab_neighbors_threshold: float = 15.0

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
            velocity: tuple[float, float], 
            n_neighbors: int, 
            last_contact_ts: float, 
            current_time: float
        ) -> bool:

        match self.scheduler_type:
            case "static":
                return self.should_send_static(current_time)
            case "dynamic_adab" | "dynamic_acab":
                return self.should_send_dynamic(battery, velocity, n_neighbors, last_contact_ts, current_time)
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
        velocity: tuple[float, float],
        n_neighbors: int,
        last_contact_ts: float,
        current_time: float,
    ) -> bool:
        
        if self.next_dynamic_interval is None:
            self.next_dynamic_interval = self.compute_interval(velocity, n_neighbors, last_contact_ts, current_time)
        
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
                mobility_score = min(1.0, speed / self.velocity_divisor)

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

        max_positive_jitter = min(self.jitter_scale, self.max_interval - bi)
        max_negative_jitter = min(self.jitter_scale, bi - self.min_interval)

        return bi + random.uniform(-max_negative_jitter, max_positive_jitter)

    # Per-node forwarding gate. Mirrors should_send() — the scheduler owns every
    # "may I use the channel?" decision, regardless of whether the payload is an
    # origination or a relay. Decoupled from origination credits (does not mutate
    # last_*_send_time) so forwarding latency stays bounded.
    #
    # Density-baseline term: keeps the expected forwarders per cascade ≈
    # forward_density_baseline regardless of how many neighbors are in range
    # (p = baseline / n_neighbors). Dynamic schedulers further scale by their
    # own back-off intensity (1 - fq, floored at 0.3) so a node that has decided
    # it's in a saturated cluster also forwards less. Static has no back-off
    # signal, so only the density baseline gates it.
    def should_forward(self, n_neighbors: int) -> bool:
        if self.forward_density_baseline <= 0 or n_neighbors <= self.forward_density_baseline:
            return True

        p = self.forward_density_baseline / n_neighbors
        if self.scheduler_type != "static":
            p *= max(0.3, 1.0 - self.last_fq)
        return random.random() < p