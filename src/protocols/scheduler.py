import random
import uuid
import math
from typing import Tuple, List
from config.config_handler import ConfigHandler
from utils import logging

class BeaconScheduler:
    def __init__(self):
        cfg = ConfigHandler()
        
        self.min_interval = cfg.get('scheduler', 'beacon_min_interval')
        self.max_interval = cfg.get('scheduler', 'beacon_max_interval')
        self.static_interval = cfg.get('scheduler', 'static_interval')
        self.scheduler_type = None
        self.default_velocity = cfg.get('buoys', 'default_velocity')
        
        self.last_static_send_time = -random.uniform(0, self.static_interval)
        self.last_dynamic_send_time = -random.uniform(0, self.min_interval)
        
        self.next_static_interval = self.static_interval
        self.next_dynamic_interval = None
    
    def get_next_check_interval(self) -> float:
        if self.scheduler_type == "static":
            return self.static_interval
        else:
            return self.next_dynamic_interval if self.next_dynamic_interval is not None else self.min_interval

    def should_send(self, buoy, battery, velocity, neighbors, current_time):
        if self.scheduler_type == "static":
            return self.should_send_static(current_time)
        elif self.scheduler_type in ["dynamic_adab", "dynamic_acab"]:
            return self.should_send_dynamic(battery, velocity, neighbors, current_time)
        elif self.scheduler_type == "dynamic_miad":
            return self.should_send_dynamic_miad(buoy, current_time)
        else:
            raise ValueError(f"Unknown scheduler type: {self.scheduler_type}")

    def should_send_static(self, current_time: float) -> bool:
        time_since_last = current_time - self.last_static_send_time
        logging.log_info(f"Static Scheduler: Time since last send: {time_since_last:.2f}s, Next interval: {self.next_static_interval:.2f}s")
        if time_since_last >= self.next_static_interval:
            self.last_static_send_time = current_time
            return True
        return False

    def should_send_dynamic(
        self,
        battery,
        velocity: Tuple[float, float],
        neighbors: List[Tuple[uuid.UUID, float, Tuple[float, float]]],
        current_time: float,
    ) -> bool:
        if self.next_dynamic_interval is None:
            self.next_dynamic_interval = self.compute_interval(velocity, neighbors, current_time)
        
        time_since_last = current_time - self.last_dynamic_send_time
        
        if time_since_last >= self.next_dynamic_interval:
            self.last_dynamic_send_time = current_time
            self.next_dynamic_interval = self.compute_interval(velocity, neighbors, current_time)
            #print(f'{self.next_dynamic_interval}')
            return True
        return False


    def should_send_dynamic_miad(
        self,
        buoy,  # Pass the Buoy instance for local data #TODO: not sending buoy
        current_time: float,
    ) -> bool:
        # MIAD Congestion Detection
        # 1. Channel busy time (average)
        avg_busy = (buoy.channel_busy_accum / buoy.channel_busy_count) if buoy.channel_busy_count > 0 else 0.0
        busy_threshold = 0.001  # TODO: think about value

        # 2. Missing beacons from neighbors 
        missing_neighbors = 0
        expected_interval = self.next_dynamic_interval if  self.next_dynamic_interval is not None else self.min_interval
        #expected_interval = self.min_interval
        for nid, last_time in buoy.last_beacon_from_neighbor.items():
            if current_time - last_time >  10 * expected_interval:
                missing_neighbors += 1

        # 3. My info in neighbor beacons (freshness)
        outdated_count = 0
        for nid, (seen_time, my_info_ts) in buoy.my_info_in_neighbor.items():
            if my_info_ts is None:
                outdated_count += 1  # Not present
            elif buoy.my_beacon_timestamp - my_info_ts >  10 * expected_interval:
                outdated_count += 1  # Info is stale

        # Congestion if any metric is above threshold 
        # TODO: think if we need 3 options inc dec and no action
        congestion = (
            avg_busy > busy_threshold or
            missing_neighbors > 5 or
            outdated_count > 5
        )

        # MIAD logic
        if self.next_dynamic_interval is None:
            self.next_dynamic_interval = self.min_interval
        miad_mult = 1.5  # Multiplicative factor
        miad_add = 0.2   # Additive decrease (seconds)

        if congestion:
            # Multiplicative increase
            self.next_dynamic_interval = min(self.max_interval, self.next_dynamic_interval * miad_mult)
        else:
            # Additive decrease
            self.next_dynamic_interval = max(self.min_interval, self.next_dynamic_interval - miad_add)
        
        print(f'{self.next_dynamic_interval}')


        time_since_last = current_time - self.last_dynamic_send_time
        if time_since_last >= self.next_dynamic_interval:
            self.last_dynamic_send_time = current_time
            return True
        return False


    def should_send_dynamic_rl(
        self,
        battery,
        velocity: Tuple[float, float],
        neighbors: List[Tuple[uuid.UUID, float, Tuple[float, float]]],
        collision_rate: float,
        current_time: float,
    )-> bool:
        context_vector = self.build_context_vector(battery, velocity, neighbors, collision_rate)
        next_interval = self.rl_model.predict(context_vector)

        reward = self.calculate_reward(
            energy_level = battery,
            collision_rate = collision_rate,
            network_discovery = len(neighbors),
            interval = next_interval,
            max_energy = 100.0,
            min_energy = 0.1,
            max_discovery = 80,
        )

        self.rl_model.update(context_vector, next_interval, reward)

        #check if it's time to send
        time_since_last = current_time - self.last_dynamic_send_time
        if time_since_last >= next_interval:
            self.last_dynamic_send_time = current_time
            return True
        return False



    def compute_interval(
        self,
        velocity: Tuple[float, float],
        neighbors: List[Tuple[uuid.UUID, float, Tuple[float, float]]],
        current_time: float,
    ) -> float:
        if self.scheduler_type == "dynamic_acab":
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
            mobility_score = min(1.0, speed / self.default_velocity)

            w_density = 0.4
            w_contact = 0.3
            w_mobility = 0.3

            combined = (w_density * density_score + 
                       w_contact * contact_score + 
                       w_mobility * (1.0 - mobility_score))
        else:
            n_neighbors = len(neighbors)
            NEIGHBORS_THRESHOLD = 15
            density_score = min(1.0, n_neighbors / NEIGHBORS_THRESHOLD)
            combined = density_score

        fq = combined * combined
        bi_min = self.static_interval
        bi = bi_min + fq * (self.max_interval - bi_min)

        jitter = random.uniform(-0.5, 0.5)
        bi_final = bi * (1 + jitter)

        return max(self.min_interval, min(bi_final, self.max_interval))