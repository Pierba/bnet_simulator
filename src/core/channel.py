import math
import random
from protocols.beacon import Beacon
from core.events import EventType
from config.config_handler import ConfigHandler
from utils import logging
from utils.metrics import Metrics

class Channel:
    # Initialization of the Channel class with configuration parameters and state variables
    def __init__(self, metrics = None, ideal_channel = None):
        cfg = ConfigHandler()
        
        # Channel parameters
        self.active_transmissions: list[tuple[Beacon, float, float]] = []
        self.metrics: Metrics = metrics
        self.buoys: list = []
        self.schedule_callback: callable = None
        
        # Setting up network parameters from configuration
        self.ideal_channel: bool = ideal_channel if ideal_channel is not None else cfg.get('simulation', 'ideal_channel')
        self.bit_rate: int = cfg.get('network', 'bit_rate')
        self.speed_of_light: float = cfg.get('network', 'speed_of_light')
        self.comm_range_max: float = cfg.get('network', 'communication_range_max')
        self.comm_range_high_prob: float = cfg.get('network', 'communication_range_high_prob')
        self.delivery_prob_high: float = cfg.get('network', 'delivery_prob_high')
        self.delivery_prob_low: float = cfg.get('network', 'delivery_prob_low')
        
        # Precomputed values for efficiency
        self.comm_range_max_sq: float = self.comm_range_max * self.comm_range_max
        self.comm_range_high_prob_sq: float = self.comm_range_high_prob * self.comm_range_high_prob
        self.grace_period = self.comm_range_max / self.speed_of_light + 1e-6

    def set_buoys(self, buoys: list):
        self.buoys = buoys

    def handle_event(self, event, sim_time: float):
        match event.event_type:
            # case EventType.CHANNEL_UPDATE:
            #     self._handle_channel_update(event, sim_time)
            case EventType.TRANSMISSION_END:
                self._handle_transmission_end(event, sim_time)
            case _:
                logging.log_error(f"Channel received unhandled event: {event.event_type}")

    def _handle_channel_update(self, event, sim_time: float):
        self.update(sim_time)
        # self.schedule_callback(
        #     sim_time + 1.0, EventType.CHANNEL_UPDATE, self  <--- no more periodic updates 
        # )

    def _handle_transmission_end(self, event, sim_time: float):
        beacon = event.data.get("beacon")
        if beacon and logging.LOGGING_ENABLED:
            logging.log_info(f"Transmission completed at {sim_time} for beacon from {str(beacon.sender_id)[:6]}")

    # Keep only the transmissions that are still active based on sim_time
    def update(self, sim_time: float):    
        self.active_transmissions = [
            (beacon, start, end)
            for (beacon, start, end) in self.active_transmissions
            if end + self.grace_period > sim_time
        ]

    def broadcast(self, beacon: Beacon, sim_time: float) -> float:
        if logging.LOGGING_ENABLED:
            logging.log_info(f"Broadcasting from {str(beacon.sender_id)[:6]} at {sim_time:.2f}s")

        self.update(sim_time)

        # Transmission time = bits / bit_rate
        transmission_time = beacon.size_bits() / self.bit_rate
        new_end_time = sim_time + transmission_time

        receivers_data = self._receivers_in_range(beacon)
        n_receivers = len(receivers_data)

        receivers_with_collisions, poisoned_count = self._detect_collisions(
            beacon, receivers_data, sim_time, new_end_time
        )

        # Record this transmission so later broadcasts can detect collisions against it
        self.active_transmissions.append((beacon, sim_time, new_end_time))
        self.schedule_callback(new_end_time, EventType.TRANSMISSION_END, self, {"beacon": beacon})

        probability_lost = self._schedule_receptions(
            beacon, receivers_data, receivers_with_collisions, new_end_time
        )

        collision_lost = len(receivers_with_collisions)
        total_lost = collision_lost + probability_lost
        actual_successful = n_receivers - total_lost

        if self.metrics:
            self.metrics.log_sent()
            self.metrics.log_potentially_sent(n_receivers)
            self.metrics.log_successful_receivers(actual_successful)
            self.metrics.log_collision(collision_lost)
            self.metrics.log_lost(total_lost)

            # Retroactive correction for earlier receptions revoked by this transmission
            if poisoned_count:
                self.metrics.log_collision(poisoned_count)
                self.metrics.log_lost(poisoned_count)
                self.metrics.log_successful_receivers(-poisoned_count)

            if logging.LOGGING_ENABLED:
                logging.log_info(f"Lost {total_lost} packets: {collision_lost} from collisions, {probability_lost} from probability")

        return new_end_time

    def _receivers_in_range(self, beacon: Beacon) -> list:
        # Active buoys (excluding the sender) within communication range of the beacon
        receivers_data = []
        beacon_x, beacon_y = beacon.position
        sender_id = beacon.sender_id
        comm_range_sq = self.comm_range_max_sq

        for buoy in self.buoys:
            if buoy.id == sender_id or not buoy.active:
                continue
            bx, by = buoy.position
            dx, dy = bx - beacon_x, by - beacon_y
            dist_sq = (dx * dx) + (dy * dy)
            if dist_sq <= comm_range_sq:
                receivers_data.append((buoy, dist_sq))

        return receivers_data

    def _detect_collisions(self, beacon: Beacon, receivers_data: list, start_time: float, end_time: float) -> tuple[set, int]:
        # Returns (receiver ids that lose this beacon, count of earlier receptions revoked)
        receivers_with_collisions = set()
        poisoned_count = 0
        sender_id = beacon.sender_id
        beacon_x, beacon_y = beacon.position
        comm_range_sq = self.comm_range_max_sq

        for existing, start, end in self.active_transmissions:
            if existing.sender_id == sender_id:
                continue
            # Skip transmissions whose time window does not overlap this one
            if not (start_time < end and start < end_time):
                continue

            # Two senders within range of each other => direct collision
            ex, ey = existing.position
            dx, dy = beacon_x - ex, beacon_y - ey
            senders_in_range = (dx * dx) + (dy * dy) <= comm_range_sq

            if senders_in_range:
                logging.log_error(f"Direct collision between {str(sender_id)[:6]} and {str(existing.sender_id)[:6]}")

            for buoy, _ in receivers_data:
                # The new beacon is lost here on a direct collision, or whenever this
                # receiver also sits in range of the existing transmission's sender
                rx, ry = buoy.position
                dx, dy = rx - ex, ry - ey
                hears_existing = (dx * dx) + (dy * dy) <= comm_range_sq

                if senders_in_range or hears_existing:
                    receivers_with_collisions.add(buoy.id)

                # Revoke the existing beacon's reception here only if one was actually
                # scheduled for this receiver -> poisoned_count counts real losses only
                if hears_existing and buoy.id in existing.scheduled_receivers:
                    existing.scheduled_receivers.discard(buoy.id)
                    poisoned_count += 1
                    logging.log_error(f"Collision at receiver {str(buoy.id)[:6]} between {str(sender_id)[:6]} and {str(existing.sender_id)[:6]}")

        return receivers_with_collisions, poisoned_count

    def _schedule_receptions(self, beacon: Beacon, receivers_data: list, receivers_with_collisions: set, end_time: float) -> int:
        # Schedules a RECEPTION per surviving receiver; returns the probabilistic-loss count
        probability_lost = 0

        for buoy, dist_sq in receivers_data:
            if buoy.id in receivers_with_collisions:
                continue

            # In a non-ideal channel, drop the packet with distance-dependent probability
            if not self.ideal_channel:
                delivery_prob = self.delivery_prob_high if dist_sq <= self.comm_range_high_prob_sq else self.delivery_prob_low
                if random.random() >= delivery_prob:
                    probability_lost += 1
                    continue

            distance = math.sqrt(dist_sq)
            propagation_delay = distance / self.speed_of_light
            reception_time = end_time + propagation_delay + 1e-9

            # Mark this receiver as a valid recipient (a later colliding transmission can revoke it)
            beacon.scheduled_receivers.add(buoy.id)
            self.schedule_callback(reception_time, EventType.RECEPTION, buoy, {"beacon": beacon})

        return probability_lost

    def is_busy(self, position: tuple[float, float], sim_time: float) -> tuple[bool, float]:
        self.update(sim_time)
        
        busy = False
        next_free_time = sim_time

        for beacon, start, end in self.active_transmissions:
            sender_position = beacon.position
            dx = position[0] - sender_position[0]
            dy = position[1] - sender_position[1]
            distance = math.hypot(dx, dy)
            
            # Skip if outside of detection range
            if distance > self.comm_range_max:
                continue
                
            # Calculate when the signal starts and stops passing through this specific position
            propagation_delay = distance / self.speed_of_light
            
            arrival_time = start + propagation_delay
            cleared_time = end + propagation_delay
            
            # Channel is busy if the wave is currently over this position. Don't stop at
            # the first hit: keep the latest clear time so the caller retries only once.
            if arrival_time <= sim_time < cleared_time:
                busy = True
                if cleared_time > next_free_time:
                    next_free_time = cleared_time

        return busy, next_free_time