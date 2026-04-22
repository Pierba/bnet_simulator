import math
import random
from typing import Tuple
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
        self.active_transmissions: list[Tuple[Beacon, float, float, int, int]] = []
        self.metrics: Metrics = metrics
        self.buoys: list = []
        self.schedule_callback: callable = None
        self.seen_attempts = set()
        self.collision_beacons = set()
        
        # Setting up network parameters from configuration
        self.ideal_channel: bool = ideal_channel if ideal_channel is not None else cfg.get('simulation', 'ideal_channel')
        self.bit_rate: int = cfg.get('network', 'bit_rate')
        self.speed_of_light: float = cfg.get('network', 'speed_of_light')
        self.comm_range_max: float = cfg.get('network', 'communication_range_max')
        self.comm_range_high_prob: float = cfg.get('network', 'communication_range_high_prob')
        
        # Precomputed squared for distance checks
        self.comm_range_max_sq: float = self.comm_range_max * self.comm_range_max
        self.comm_range_high_prob_sq: float = self.comm_range_high_prob * self.comm_range_high_prob
        
        self.delivery_prob_high: float = cfg.get('network', 'delivery_prob_high')
        self.delivery_prob_low: float = cfg.get('network', 'delivery_prob_low')

    def set_buoys(self, buoys: list):
        self.buoys = buoys

    def handle_event(self, event, sim_time: float):
        match event.event_type:
            case EventType.CHANNEL_UPDATE:
                self._handle_channel_update(event, sim_time)
            case EventType.TRANSMISSION_END:
                self._handle_transmission_end(event, sim_time)
            case _:
                logging.log_error(f"Channel received unhandled event: {event.event_type}")

    def _handle_channel_update(self, event, sim_time: float):
        self.update(sim_time)
        self.schedule_callback(
            sim_time + 1.0, EventType.CHANNEL_UPDATE, self
        )

    def _handle_transmission_end(self, event, sim_time: float):
        beacon = event.data.get("beacon")
        if beacon:
            logging.log_info(f"Transmission completed at {sim_time} for beacon from {str(beacon.sender_id)[:6]}")

    def update(self, sim_time: float):
        expired_indices = []
        max_delay = self.comm_range_max / self.speed_of_light
        grace_period = max_delay + 1e-6
        
        for i, (beacon, start, end, potential_count, processed_count) in enumerate(self.active_transmissions):
            if end + grace_period > sim_time:
                continue
            expired_indices.append(i)
            
            if not self.ideal_channel:
                continue
            beacon_key = (beacon.sender_id, beacon.timestamp)
            
            if beacon_key in self.collision_beacons:
                continue

            unprocessed = potential_count - processed_count
            for _ in range(unprocessed):
                if self.metrics:
                    self.metrics.log_actually_received(beacon.sender_id)
                    logging.log_info(f"Ideal channel: marking {unprocessed} unreached as received for {str(beacon.sender_id)[:6]}")

        for idx in sorted(expired_indices, reverse=True):
            self.active_transmissions.pop(idx)

    def broadcast(self, beacon: Beacon, sim_time: float):
        logging.log_info(f"Broadcasting from {str(beacon.sender_id)[:6]} at {sim_time:.2f}s")
        
        if self.metrics:
            self.metrics.log_sent()

        # Inverse formula to get transmission time: time = bits / bit_rate
        transmission_time = beacon.size_bits() / self.bit_rate
        new_start_time = sim_time
        new_end_time = sim_time + transmission_time

        # Find all buoy receivers in range of the sender
        receivers_in_range = [
            buoy for buoy in self.buoys
            if buoy.id != beacon.sender_id and self.in_range(beacon.position, buoy.position)
        ]
        n_receivers = len(receivers_in_range)
        
        if self.metrics:
            self.metrics.log_potentially_sent(beacon.sender_id, n_receivers)

        # Check for collisions with active transmissions
        receivers_with_collisions = set()
        beacon_key = (beacon.sender_id, beacon.timestamp)
        receiver_ids = {r.id for r in receivers_in_range}

        for existing, start, end, _, _ in self.active_transmissions:
            if beacon.sender_id == existing.sender_id:
                continue
            
            # ===============
            #  TIME OVERLAP
            # ===============
            # Check if the time windows of the two transmissions overlap in timing
            if new_start_time > end or start > new_end_time:
                continue
            
            existing_key = (existing.sender_id, existing.timestamp)
        
            # ================
            #  SPACE OVERLAP
            # ================
            # Check if the beacons are close enough to cause a direct collision
            if self.in_range(beacon.position, existing.position):
                logging.log_error(f"Direct collision between {str(beacon.sender_id)[:6]} and {str(existing.sender_id)[:6]}")
                # Mark both beacons as collided
                self.collision_beacons.add(beacon_key)
                self.collision_beacons.add(existing_key)

                # All receivers in range will be affected by the collision because of csma/ca rules
                receivers_with_collisions.update(receiver_ids)
            
            else: 
                if len(receivers_with_collisions) == len(receiver_ids):
                    self.collision_beacons.add(existing_key)
                    continue  # All receivers already affected by collisions, no need to check further
                    
                # If not a direct collision, check if they cause a collision at any receiver in range
                for receiver in receivers_in_range:
                    if not self.in_range(receiver.position, existing.position):
                        continue

                    # If this receiver is affected by the existing transmission it will collide with the new beacon
                    if receiver.id not in receivers_with_collisions:
                        logging.log_error(f"Collision at receiver {str(receiver.id)[:6]} between {str(beacon.sender_id)[:6]} and {str(existing.sender_id)[:6]}")
                        receivers_with_collisions.add(receiver.id)
                        self.collision_beacons.add(beacon_key)
                    self.collision_beacons.add(existing_key)

        # Calculate successful receivers after accounting for collisions
        successful_receivers = n_receivers - len(receivers_with_collisions)

        # Log the calculated transmission informations
        self.active_transmissions.append((beacon, new_start_time, new_end_time, n_receivers, successful_receivers))
        
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
        
        for receiver in receivers_in_range:
            # If this receiver is affected by a collision skip it
            if receiver.id in receivers_with_collisions:
                continue
                
            dx = receiver.position[0] - beacon.position[0]
            dy = receiver.position[1] - beacon.position[1]
            distance_sq = (dx * dx) + (dy * dy)
            
            # Calculate probabilistic loss
            if not self.ideal_channel:
                # Determine delivery probability based on distance
                delivery_prob = self.delivery_prob_high if distance_sq <= self.comm_range_high_prob_sq else self.delivery_prob_low
                # If the random value exceeds the prob consder the packet lost
                if random.random() >= delivery_prob:
                    probability_lost += 1
                    continue
            
            # Compute propagation timing if the packet actually survived
            distance = math.sqrt(distance_sq)
            propagation_delay = distance / self.speed_of_light
            reception_time = new_end_time + propagation_delay + 1e-9
            
            # Schedule the reception event for this receiver
            self.schedule_callback(
                reception_time,
                EventType.RECEPTION, 
                receiver,
                {"beacon": beacon, "collision_checked": True}
            )
        
        total_lost = collision_lost + probability_lost
        
        if self.metrics:
            if collision_lost > 0:
                self.metrics.log_collision(collision_lost)
            
            if total_lost > 0:
                self.metrics.log_lost(total_lost)
                logging.log_info(f"Lost {total_lost} packets: {collision_lost} from collisions, {probability_lost} from probability")

    def is_busy(self, position: Tuple[float, float], sim_time: float) -> bool:
        for beacon, start, end, _, _ in self.active_transmissions:
            if start <= sim_time <= end:
                sender_position = beacon.position
                
                dx = position[0] - sender_position[0]
                dy = position[1] - sender_position[1]
                distance_sq = (dx * dx) + (dy * dy)
                
                wavefront_radius = self.speed_of_light * (sim_time - start)
                wavefront_radius_sq = wavefront_radius * wavefront_radius
                
                if distance_sq <= wavefront_radius_sq and distance_sq <= self.comm_range_high_prob_sq:
                    return True
                
        return False

    def in_range(self, pos1: Tuple[float, float], pos2: Tuple[float, float]) -> bool:
        dx = pos1[0] - pos2[0]
        dy = pos1[1] - pos2[1]
        return (dx * dx) + (dy * dy) <= self.comm_range_max_sq