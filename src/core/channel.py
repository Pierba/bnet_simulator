import math
import random
from typing import Tuple
from protocols.beacon import Beacon
from core.events import EventType
from config.config_handler import ConfigHandler
from utils import logging

class Channel:
    # Initialization of the Channel class with configuration parameters and state variables
    def __init__(self, metrics = None, ideal_channel = None):
        cfg = ConfigHandler()
        
        self.active_transmissions = []
        self.metrics = metrics
        self.buoys = []
        self.schedule_callback = None
        self.seen_attempts = set()
        self.collision_beacons = set()
        
        # Setting up channel parameters from configuration
        self.ideal_channel = ideal_channel if ideal_channel is not None else cfg.get('simulation', 'ideal_channel')
        self.bit_rate = cfg.get('network', 'bit_rate')
        self.speed_of_light = cfg.get('network', 'speed_of_light')
        self.comm_range_max = cfg.get('network', 'communication_range_max')
        self.comm_range_high_prob = cfg.get('network', 'communication_range_high_prob')
        self.delivery_prob_high = cfg.get('network', 'delivery_prob_high')
        self.delivery_prob_low = cfg.get('network', 'delivery_prob_low')

    def set_buoys(self, buoys):
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
            sim_time + 1.0, event.event_type, self
        )

    def _handle_transmission_end(self, event, sim_time: float):
        beacon = event.data.get("beacon")
        if beacon:
            logging.log_info(f"Transmission completed for beacon from {str(beacon.sender_id)[:6]}")

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

    def broadcast(self, beacon: Beacon, sim_time: float) -> bool:
        logging.log_info(f"Broadcasting from {str(beacon.sender_id)[:6]} at {sim_time:.2f}s")
        
        if self.metrics:
            self.metrics.log_sent()

        transmission_time = beacon.size_bits() / self.bit_rate
        new_end_time = sim_time + transmission_time

        receivers_in_range = [
            buoy for buoy in self.buoys
            if buoy.id != beacon.sender_id and self.in_range(beacon.position, buoy.position)
        ]
        n_receivers = len(receivers_in_range)
        
        if self.metrics:
            self.metrics.log_potentially_sent(beacon.sender_id, n_receivers)

        beacon_key = (beacon.sender_id, beacon.timestamp)
        receivers_with_collisions = set()

        for i, (existing, start, end, _, _) in enumerate(self.active_transmissions):
            if beacon.sender_id == existing.sender_id:
                continue
            
            time_overlap = (sim_time <= end) and (start <= new_end_time)
            if not time_overlap:
                continue
            
            existing_key = (existing.sender_id, existing.timestamp)
            
            if self.in_range(beacon.position, existing.position):
                logging.log_error(f"Direct collision between {str(beacon.sender_id)[:6]} and {str(existing.sender_id)[:6]}")
                self.collision_beacons.add(beacon_key)
                self.collision_beacons.add(existing_key)
                
                for receiver in receivers_in_range:
                    receivers_with_collisions.add(receiver.id)   
            else:
                for receiver in receivers_in_range:
                    if self.in_range(receiver.position, existing.position):
                        logging.log_error(f"Collision at receiver {str(receiver.id)[:6]} between {str(beacon.sender_id)[:6]} and {str(existing.sender_id)[:6]}")
                        receivers_with_collisions.add(receiver.id)
                        self.collision_beacons.add(beacon_key)
                        self.collision_beacons.add(existing_key)

        successful_receivers = 0
        self.active_transmissions.append((beacon, sim_time, new_end_time, n_receivers, successful_receivers))
        
        self.schedule_callback(
            new_end_time, 
            EventType.TRANSMISSION_END, 
            self,
            {"beacon": beacon}
        )
        
        total_lost = 0
        collision_lost = len(receivers_with_collisions)
        probability_lost = 0
        
        for receiver in receivers_in_range:
            dx = receiver.position[0] - beacon.position[0]
            dy = receiver.position[1] - beacon.position[1]
            distance = math.hypot(dx, dy)
            propagation_delay = distance / self.speed_of_light
            reception_time = new_end_time + propagation_delay + 1e-9
            
            collision_loss = receiver.id in receivers_with_collisions
            will_receive = False
            
            if self.ideal_channel:
                will_receive = not collision_loss
            else:
                probability_loss = False
                
                if not collision_loss:
                    random_val = random.random()
                    
                    if distance <= self.comm_range_high_prob:
                        probability_loss = random_val >= self.delivery_prob_high
                    elif distance <= self.comm_range_max:
                        probability_loss = random_val >= self.delivery_prob_low
                    
                    if probability_loss:
                        probability_lost += 1
                
                will_receive = not (collision_loss or probability_loss)
            
            if will_receive:
                self.schedule_callback(
                    reception_time,
                    EventType.RECEPTION, 
                    receiver,
                    {"beacon": beacon, "collision_checked": True}
                )
        
        total_lost = collision_lost + probability_lost
        
        if self.metrics:
            if collision_lost > 0:
                for _ in range(collision_lost):
                    self.metrics.log_collision()
            
            if total_lost > 0:
                self.metrics.log_lost(total_lost)
                logging.log_info(f"Lost {total_lost} packets: {collision_lost} from collisions, {probability_lost} from probability")
        
        return True

    def is_busy(self, position: Tuple[float, float], sim_time: float) -> bool:
        for beacon, start, end, _, _ in self.active_transmissions:
            if start <= sim_time <= end:
                sender_position = beacon.position
                
                dx = position[0] - sender_position[0]
                dy = position[1] - sender_position[1]
                distance = math.hypot(dx, dy)
                
                wavefront_radius = self.speed_of_light * (sim_time - start)
                detection_range = self.comm_range_high_prob
                
                if distance <= wavefront_radius and distance <= detection_range:
                    return True
                
        return False

    def in_range(self, pos1: Tuple[float, float], pos2: Tuple[float, float]) -> bool:
        dx = pos1[0] - pos2[0]
        dy = pos1[1] - pos2[1]
        distance = math.hypot(dx, dy)
        return distance <= self.comm_range_max