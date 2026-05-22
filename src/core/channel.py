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
        
        # Refresh the current transmissions
        self.update(sim_time)            

        # Inverse formula to get transmission time: time = bits / bit_rate
        new_start_time = sim_time
        transmission_time = beacon.size_bits() / self.bit_rate
        new_end_time = sim_time + transmission_time
        
        # Precompute distances and keep track of receivers in range
        receivers_data = []
        beacon_x, beacon_y = beacon.position
        comm_range_sq = self.comm_range_max_sq
        sender_id = beacon.sender_id

        # Find all buoy receivers in range of the sender
        for buoy in self.buoys:
            # Skip the sender itself, it cannot receive its own transmission
            if buoy.id == sender_id:
                continue
       
            # Skip inactive buoys, they cannot receive or cause collisions
            if not buoy.active:
                continue

            bx, by = buoy.position
            dx = bx - beacon_x
            dy = by - beacon_y
            dist_sq = (dx * dx) + (dy * dy)
            if dist_sq <= comm_range_sq:
                receivers_data.append((buoy, dist_sq))
                
        n_receivers = len(receivers_data)

        # Check for collisions with active transmissions
        receivers_with_collisions = set()      # receivers that lose THIS (new) beacon
        poisoned_count = 0                     # already-scheduled receptions invalidated below

        for existing, start, end in self.active_transmissions:
            if existing.sender_id == sender_id:
                continue # Avoid self-collision check, a buoy's own transmission should not collide with itself
            
            # ===============
            #  TIME OVERLAP
            # ===============
            # Check if the time windows of the two transmissions overlap in timing
            if not (new_start_time < end and start < new_end_time):
                continue

            # ================
            #  SPACE OVERLAP
            # ================
            # Check if the beacons are close enough to cause a direct collision
            ex, ey = existing.position
            dx = beacon_x - ex
            dy = beacon_y - ey
            
            senders_in_range = (dx * dx) + (dy * dy) <= comm_range_sq

            if senders_in_range:
                logging.log_error(f"Direct collision between {str(sender_id)[:6]} and {str(existing.sender_id)[:6]}")

            for buoy, _ in receivers_data:
                # The new beacon is lost here on a direct collision, or whenever this
                # receiver also sits in range of the existing transmission's sender
                rx, ry = buoy.position
                dx = rx - ex
                dy = ry - ey
                hears_existing = (dx * dx) + (dy * dy) <= comm_range_sq
                    
                if senders_in_range or hears_existing:
                    receivers_with_collisions.add(buoy.id)

                # Revoke the existing beacon's reception here only if one was actually
                # scheduled for this receiver -> poisoned_count counts real losses only
                if hears_existing and buoy.id in existing.scheduled_receivers:
                    existing.scheduled_receivers.discard(buoy.id)
                    poisoned_count += 1
                    logging.log_error(f"Collision at receiver {str(buoy.id)[:6]} between {str(sender_id)[:6]} and {str(existing.sender_id)[:6]}")

        # Log the calculated transmission informations
        self.active_transmissions.append((beacon, new_start_time, new_end_time))
        
        # Schedule the end of transmission event
        self.schedule_callback(
            new_end_time, 
            EventType.TRANSMISSION_END, 
            self,
            {"beacon": beacon}
        )
        
        # ==================
        #  PROBABILITY LOSS
        # ==================
        # Schedule receptions for all receivers that are in range and not affected by collisions or probabilistic loss
        collision_lost = len(receivers_with_collisions)
        probability_lost = 0
        
        ideal_channel = self.ideal_channel
        high_prob_sq = self.comm_range_high_prob_sq
        prob_high = self.delivery_prob_high
        prob_low = self.delivery_prob_low
        speed_of_light = self.speed_of_light

        for buoy, dist_sq in receivers_data:
            # If this receiver is affected by a collision skip it
            if buoy.id in receivers_with_collisions:
                continue
                
            # Calculate probabilistic loss
            if not ideal_channel:
                # Determine delivery probability based on distance
                delivery_prob = prob_high if dist_sq <= high_prob_sq else prob_low
                # If the random value exceeds the prob consder the packet lost
                if random.random() >= delivery_prob:
                    probability_lost += 1
                    continue
            
            distance = math.sqrt(dist_sq)
            
            # Compute propagation timing if the packet actually survived
            propagation_delay = distance / speed_of_light
            reception_time = new_end_time + propagation_delay + 1e-9
            
            # Schedule the reception event and mark this receiver as a valid recipient
            # (a later colliding transmission can still revoke it before arrival)
            beacon.scheduled_receivers.add(buoy.id)
            self.schedule_callback(
                reception_time,
                EventType.RECEPTION, 
                buoy,
                {"beacon": beacon}
            )
        
        total_lost = collision_lost + probability_lost
        actual_successful = n_receivers - total_lost
        
        if self.metrics:
            self.metrics.log_sent()
            self.metrics.log_potentially_sent(n_receivers)
            self.metrics.log_successful_receivers(actual_successful)
            self.metrics.log_collision(collision_lost)
            
            self.metrics.log_lost(total_lost)

            # Retroactive correction: receivers that already had a reception scheduled for
            # an earlier transmission collided with this one -> move them from "successful"
            # to "collided/lost" so the channel-side counters stay consistent
            if poisoned_count:
                self.metrics.log_collision(poisoned_count)
                self.metrics.log_lost(poisoned_count)
                self.metrics.log_successful_receivers(-poisoned_count)

            if logging.LOGGING_ENABLED:
                logging.log_info(f"Lost {total_lost} packets: {collision_lost} from collisions, {probability_lost} from probability")

        return new_end_time

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