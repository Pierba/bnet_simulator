import uuid
import random
import math
from collections import deque
from enum import Enum
from typing import Tuple
from protocols.scheduler import BeaconScheduler
from protocols.beacon import Beacon
from core.events import EventType
from config.config_handler import ConfigHandler
from utils import logging

class BuoyState(Enum):
    SLEEPING = 0
    RECEIVING = 1
    WAITING_DIFS = 2
    BACKOFF = 3

class Buoy:
    def __init__(
        self,
        channel,
        position: Tuple[float, float] = (0.0, 0.0),
        is_mobile: bool = False,
        battery: float = None,
        velocity: Tuple[float, float] = (0.0, 0.0),
        metrics = None,
        scheduler_type: str = None
    ):
        cfg = ConfigHandler()
        
        self.id = uuid.uuid4()
        self.position = position
        self.is_mobile = is_mobile
        self.battery = battery if battery is not None else cfg.get('buoys', 'default_battery')
        self.velocity = velocity
        self.neighbors = []  # Direct neighbors (1-hop, beacons we received directly)
        self.scheduler = BeaconScheduler()
        self.channel = channel
        self.state = BuoyState.RECEIVING
        self.metrics = metrics
        self.simulator = None

        self.difs_time = cfg.get('csma', 'difs_time')
        self.slot_time = cfg.get('csma', 'slot_time')
        self.cw = cfg.get('csma', 'cw')
        
        self.neighbor_timeout = cfg.get('scheduler', 'neighbor_timeout')
        self.world_width = cfg.get('world', 'width')
        self.world_height = cfg.get('world', 'height')
        self.speed_of_light = cfg.get('network', 'speed_of_light')
        self.comm_range_max = cfg.get('network', 'communication_range_max')

        # --- Energy Model ---
        energy_enabled_for = cfg.get('energy', 'enable_for_protocols') or []
        self.enable_energy_model = scheduler_type in energy_enabled_for
        self.initial_battery = self.battery
        self.total_energy_consumed = 0.0
        self.is_dead = False
        self.transmission_energy = cfg.get('energy', 'transmission_energy')
        self.reception_energy = cfg.get('energy', 'reception_energy')
        self.idle_listening_energy = cfg.get('energy', 'idle_listening_energy')
        self.min_battery_threshold = cfg.get('energy', 'min_battery_threshold')

        # --- AIMD Congestion Tracking ---
        self.channel_busy_accum = 0.0  # Total busy time accumulated before sending
        self.channel_busy_count = 0     # Number of send attempts measured
        self.last_channel_busy_start = None  # For measuring busy periods

        self.last_beacon_from_neighbor = {}  # {neighbor_id: last_time}
        self.my_info_in_neighbor = {}        # {neighbor_id: (last_time_seen, my_info_timestamp)}
        # Keep a short history of received beacon timestamps per neighbor
        # Used to determine if a neighbor was actively sending before a gap
        self.beacon_history_from_neighbor = {}  # {neighbor_id: deque([t1, t2, ...])}

        # For AIMD: store how up-to-date our info is in neighbor beacons
        self.my_beacon_timestamp = 0.0      # Last time we sent a beacon
        
        self.multihop_mode = cfg.get('simulation', 'multihop_mode')
        self.multihop_limit = cfg.get('simulation', 'multihop_limit')

        self.backoff_time = 0.0
        self.backoff_remaining = 0.0
        self.next_try_time = 0.0
        self.want_to_send = False
        self.scheduler_decision_time = 0.0
        
        # Multihop append mode: store discovered nodes (node_id, timestamp, position)
        # These are nodes we learned about from other beacons' neighbor lists
        self.discovered_nodes = []  # [(node_id, timestamp, position), ...]
        
        # Multihop forwarded mode: track seen beacons to avoid forwarding duplicates
        self.forwarded_beacons = set()
        
    def _consume_energy(self, amount: float):
        """Consume energy from battery. Mark buoy as dead if battery depletes."""
        if not self.enable_energy_model:
            return
        
        self.battery -= amount
        self.total_energy_consumed += amount
        
        if self.battery <= self.min_battery_threshold:
            self.battery = 0.0
            self.is_dead = True
            logging.log_info(f"Buoy {str(self.id)[:6]} died (battery depleted)")
            if self.metrics:
                self.metrics.log_buoy_dead(self.id)
    def handle_event(self, event, sim_time: float):
        #todo: check with prof
        # Dead buoys don't process events (except BUOY_MOVEMENT for mobility)
        if self.is_dead and event.event_type != EventType.BUOY_MOVEMENT:
            return
        
        logging.log_debug(f"Buoy {str(self.id)[:6]} handling event: {event.event_type} at time {sim_time:.4f}")
        handlers = {
            EventType.SCHEDULER_CHECK: self._handle_scheduler_check,
            EventType.CHANNEL_SENSE: self._handle_channel_sense,
            EventType.DIFS_COMPLETION: self._handle_difs_completion,
            EventType.BACKOFF_COMPLETION: self._handle_backoff_completion,
            EventType.TRANSMISSION_START: self._handle_transmission_start,
            EventType.RECEPTION: self._handle_reception,
            EventType.NEIGHBOR_CLEANUP: self._handle_neighbor_cleanup,
            EventType.BUOY_MOVEMENT: self._handle_buoy_movement
        }
        
        handler = handlers.get(event.event_type)
        if handler:
            handler(event, sim_time)
        else:
            logging.log_error(f"Buoy {str(self.id)[:6]} received unhandled event: {event.event_type}")

    def _handle_scheduler_check(self, event, sim_time: float):


        should_send = self.scheduler.should_send(self,
            self.battery, self.velocity, self.neighbors, sim_time
        )
        
        if should_send:
            self.want_to_send = True
            self.scheduler_decision_time = sim_time
            self.simulator.schedule_event(
                sim_time, EventType.CHANNEL_SENSE, self
            )
        
        next_check_interval = self.scheduler.get_next_check_interval()
        self.simulator.schedule_event(
            sim_time + next_check_interval, EventType.SCHEDULER_CHECK, self
        )

    def _handle_channel_sense(self, event, sim_time: float):
        # Check if this is a forwarding request
        forward_beacon = event.data.get("forward_beacon")
        
        if forward_beacon:
            # Forward mode: create forwarded beacon
            if self.channel.is_busy(self.position, sim_time):
                self.simulator.schedule_event(
                    sim_time + 0.01, EventType.CHANNEL_SENSE, self, {"forward_beacon": forward_beacon}
                )
            else:
                # Create and broadcast forwarded beacon
                forwarded = self.forward_beacon(forward_beacon, sim_time)
                self.channel.broadcast(forwarded, sim_time)
                logging.log_info(f"Buoy {str(self.id)[:6]} forwarded beacon from {str(forward_beacon.origin_id)[:6]}, hops left: {forwarded.hop_limit}")
        elif self.want_to_send:
            # Normal transmission
            if self.channel.is_busy(self.position, sim_time):
                # Start or continue measuring busy period
                if self.last_channel_busy_start is None:
                    self.last_channel_busy_start = sim_time
                self.simulator.schedule_event(
                    sim_time + 0.01, EventType.CHANNEL_SENSE, self
                )
            else:
                # If we were waiting, accumulate busy time
                if self.last_channel_busy_start is not None:
                    busy_time = sim_time - self.last_channel_busy_start
                    self.channel_busy_accum += busy_time
                    self.channel_busy_count += 1
                    self.last_channel_busy_start = None
                self.state = BuoyState.WAITING_DIFS
                self.simulator.schedule_event(
                    sim_time + self.difs_time, EventType.DIFS_COMPLETION, self
                )

    def _handle_difs_completion(self, event, sim_time: float):
        if not self.want_to_send or self.state != BuoyState.WAITING_DIFS:
            return
            
        if self.channel.is_busy(self.position, sim_time):
            self.state = BuoyState.RECEIVING
            self.simulator.schedule_event(sim_time, EventType.CHANNEL_SENSE, self)
        else:
            # Only generate new random backoff if we don't have remaining backoff
            if self.backoff_remaining <= 0:
                backoff_slots = random.randint(0, self.cw - 1)
                backoff_time = backoff_slots * self.slot_time
                self.backoff_time = backoff_time
                self.backoff_remaining = backoff_time
            # Otherwise, use the remaining backoff from previous interruption
            
            self.state = BuoyState.BACKOFF
            
            self.simulator.schedule_event(
                sim_time + self.backoff_remaining, 
                EventType.BACKOFF_COMPLETION, 
                self,
                {"backoff_start_time": sim_time}
            )

    def _handle_backoff_completion(self, event, sim_time: float):
        if not self.want_to_send or self.state != BuoyState.BACKOFF:
            return
            
        if self.channel.is_busy(self.position, sim_time):
            # Calculate how much backoff time we actually consumed
            backoff_start = event.data.get("backoff_start_time", sim_time - self.backoff_remaining)
            elapsed = sim_time - backoff_start
            self.backoff_remaining = max(0, self.backoff_remaining - elapsed)
            self.state = BuoyState.RECEIVING
            
            # Wait for channel to become idle, then resume with DIFS (which will resume backoff)
            self.simulator.schedule_event(
                sim_time + 0.01, EventType.CHANNEL_SENSE, self
            )
        else:
            # Backoff completed successfully, transmit
            self.backoff_remaining = 0.0  # Reset for next transmission
            self.simulator.schedule_event(
                sim_time, EventType.TRANSMISSION_START, self
            )

    def _handle_transmission_start(self, event, sim_time: float):
        if not self.want_to_send:
            return
        
        beacon = self.create_beacon(sim_time)
        success = self.channel.broadcast(beacon, sim_time)
        self.my_beacon_timestamp = sim_time  # Track last beacon sent
        self.want_to_send = False
        self.backoff_remaining = 0.0  # Reset backoff for next transmission cycle
        self.state = BuoyState.RECEIVING
        # Don't clear discovered_nodes - they persist like neighbors
        
        # Consume transmission energy
        self._consume_energy(self.transmission_energy)
        
        if success and self.metrics:
            latency = sim_time - self.scheduler_decision_time
            self.metrics.record_scheduler_latency(latency)

    def _handle_reception(self, event, sim_time: float):
        beacon = event.data.get("beacon")
        if not beacon:
            return
        
        #todo: check with prof
        # Consume reception energy based on beacon transmission time
        cfg = ConfigHandler()
        bit_rate = cfg.get('network', 'bit_rate')
        reception_time = beacon.size_bits() / bit_rate if bit_rate > 0 else 0.001
        self._consume_energy(self.reception_energy * reception_time)
        
        # --- AIMD: Track beacon reception from neighbors ---
        self.last_beacon_from_neighbor[beacon.sender_id] = sim_time
        # Update per-neighbor short history
        dq = self.beacon_history_from_neighbor.get(beacon.sender_id)

        #if there is no history for this neighbor, create a new deque
        #maxlen=4 means we only keep the last 4 beacon reception times from this neighbor, which is enough to determine if they were actively sending before a gap
        if dq is None:
            dq = deque(maxlen=4)
            self.beacon_history_from_neighbor[beacon.sender_id] = dq
        dq.append(sim_time)
        # Check if our info appears in neighbor's beacon
        found = False
        my_info_ts = None
        for neighbor_id, neighbor_ts, _ in beacon.neighbors:
            if neighbor_id == self.id:
                found = True
                my_info_ts = neighbor_ts
                break
        if found:
            self.my_info_in_neighbor[beacon.sender_id] = (sim_time, my_info_ts)
        else:
            self.my_info_in_neighbor[beacon.sender_id] = (sim_time, None)
    
        collision = False
        collision_checked = event.data.get("collision_checked", False)
    
        if not collision_checked:
            COLLISION_WINDOW = 1e-5
            
            for tx_beacon, start, end, _, _ in self.channel.active_transmissions:
                if tx_beacon.sender_id == beacon.sender_id and tx_beacon.timestamp == beacon.timestamp:
                    continue
            
                if sim_time < start:
                    continue
                
                dx = self.position[0] - tx_beacon.position[0]
                dy = self.position[1] - tx_beacon.position[1]
                distance = math.hypot(dx, dy)
            
                if distance > self.comm_range_max:
                    continue
                
                propagation_delay = distance / self.speed_of_light
                arrival_time = end + propagation_delay
            
                if abs(arrival_time - sim_time) < COLLISION_WINDOW:
                    logging.log_error(f"Collision detected at receiver {str(self.id)[:6]} between {str(beacon.sender_id)[:6]} and {str(tx_beacon.sender_id)[:6]}")
                    collision = True
                    break
    
        if collision:
            return
        
        # Track all unique nodes discovered from this beacon (for metrics)
        discovered_nodes = set()
        
        # 1. Always discover the direct sender
        discovered_nodes.add(beacon.sender_id)
        
        # 2. In forward mode, also discover the origin if different
        if self.multihop_mode == 'forwarded' and beacon.origin_id is not None:
            if beacon.origin_id != self.id and beacon.origin_id != beacon.sender_id:
                discovered_nodes.add(beacon.origin_id)
        
        # 3. Discover all nodes from the beacon's neighbor list
        for neighbor_id, neighbor_ts, neighbor_pos in beacon.neighbors:
            if neighbor_id != self.id:  # Don't count self
                discovered_nodes.add(neighbor_id)
        
        # Update direct neighbors (by sender_id) - this is 1-hop connectivity
        updated = False
        for i, (nid, _, _) in enumerate(self.neighbors):
            if nid == beacon.sender_id:
                self.neighbors[i] = (nid, sim_time, beacon.position)
                updated = True
                break
        if not updated:
            self.neighbors.append((beacon.sender_id, sim_time, beacon.position))
        
        # Multihop append mode: collect discovered nodes from beacon's neighbor list
        # These are NOT direct neighbors, but nodes we learned about indirectly
        if self.multihop_mode == 'append':
            for neighbor_id, neighbor_ts, neighbor_pos in beacon.neighbors:
                if neighbor_id == self.id or neighbor_id == beacon.sender_id:
                    continue
                
                # Update or add to discovered_nodes list with metadata
                updated = False
                for i, (nid, _, _) in enumerate(self.discovered_nodes):
                    if nid == neighbor_id:
                        # Update with latest timestamp and position
                        self.discovered_nodes[i] = (neighbor_id, neighbor_ts, neighbor_pos)
                        updated = True
                        break
                
                if not updated:
                    self.discovered_nodes.append((neighbor_id, neighbor_ts, neighbor_pos))
        
        # Multihop forwarded mode: forward beacon WITHOUT modification if hop_limit > 0
        if self.multihop_mode == 'forwarded' and beacon.hop_limit > 0:
            beacon_key = (beacon.origin_id, beacon.timestamp)
            if beacon_key not in self.forwarded_beacons:
                self.forwarded_beacons.add(beacon_key)
                # Schedule forwarding immediately
                self.simulator.schedule_event(
                    sim_time + 0.001,
                    EventType.CHANNEL_SENSE,
                    self,
                    {"forward_beacon": beacon}
                )
        
        key = (self.id, beacon.sender_id, beacon.timestamp)
        if key not in self.channel.seen_attempts:
            self.channel.seen_attempts.add(key)
        
            for i, (tx_beacon, start, end, potential_count, processed_count) in enumerate(self.channel.active_transmissions):
                if tx_beacon.sender_id == beacon.sender_id and tx_beacon.timestamp == beacon.timestamp:
                    self.channel.active_transmissions[i] = (tx_beacon, start, end, potential_count, processed_count + 1)
                    break
        
            if self.metrics:
                # Track all unique nodes discovered from this beacon
                if self.id not in self.metrics.unique_nodes_per_buoy:
                    self.metrics.unique_nodes_per_buoy[self.id] = set()
                self.metrics.unique_nodes_per_buoy[self.id].update(discovered_nodes)
                
                # Log reception for latency tracking
                self.metrics.log_received(
                    sender_id=beacon.sender_id,
                    timestamp=beacon.timestamp,
                    receive_time=sim_time,
                    receiver_id=None
                )
                
                # Track for delivery ratio
                self.metrics.log_actually_received(beacon.sender_id)

    def _handle_neighbor_cleanup(self, event, sim_time: float):
        # Cleanup direct neighbors
        self.neighbors = [
            (nid, ts, pos) for nid, ts, pos in self.neighbors
            if sim_time - ts <= self.neighbor_timeout
        ]
        
        # In append mode, cleanup old discovered nodes
        if self.multihop_mode == 'append':
            self.discovered_nodes = [
                (nid, ts, pos) for nid, ts, pos in self.discovered_nodes
                if sim_time - ts <= self.neighbor_timeout
            ]
        
        self.simulator.schedule_event(
            sim_time + self.neighbor_timeout, EventType.NEIGHBOR_CLEANUP, self
        )

    def _handle_buoy_movement(self, event, sim_time: float):
        if not self.is_mobile:
            return
            
        dt = 0.1
        x, y = self.position
        vx, vy = self.velocity
        
        new_x = x + vx * dt
        new_y = y + vy * dt
        
        if new_x < 0 or new_x > self.world_width:
            self.velocity = (-vx, vy)
        if new_y < 0 or new_y > self.world_height:
            self.velocity = (vx, -vy)
            
        vx, vy = self.velocity
        self.position = (x + vx * dt, y + vy * dt)
        
        self.simulator.schedule_event(
            sim_time + dt, EventType.BUOY_MOVEMENT, self
        )
    
    def create_beacon(self, sim_time: float) -> Beacon:
        all_neighbors = self.neighbors.copy()
        
        # In append mode, add discovered nodes to the neighbor list
        # These are nodes learned from other beacons (not direct 1-hop neighbors)
        if self.multihop_mode == 'append':
            for node_id, node_ts, node_pos in self.discovered_nodes:
                # Check if not already in neighbors (direct or discovered)
                if not any(nid == node_id for nid, _, _ in all_neighbors):
                    # Add with the metadata we have (timestamp and position from when we learned about them)
                    all_neighbors.append((node_id, node_ts, node_pos))
        
        # Set origin and hop_limit for forwarded mode
        origin_id = None
        hop_limit = 0
        
        if self.multihop_mode == 'forwarded':
            origin_id = self.id
            hop_limit = self.multihop_limit
        
        return Beacon(
            sender_id=self.id,
            mobile=self.is_mobile,
            position=self.position,
            battery=self.battery,
            neighbors=all_neighbors,
            timestamp=sim_time,
            origin_id=origin_id,
            hop_limit=hop_limit
        )
    
    def forward_beacon(self, original_beacon: Beacon, sim_time: float) -> Beacon:
        # In forward mode, forward WITHOUT modification (only decrement hop_limit)
        # Forwarder becomes sender for channel purposes, but packet content unchanged
        return Beacon(
            sender_id=self.id,  # Forwarder becomes sender for transmission
            mobile=original_beacon.mobile,  # Keep original mobility
            position=self.position,  # Use forwarder's position for range calculation
            battery=self.battery,  # Forwarder's battery for transmission
            neighbors=original_beacon.neighbors,  # KEEP ORIGINAL NEIGHBORS - NO MODIFICATION
            timestamp=original_beacon.timestamp,  # Keep original timestamp
            origin_id=original_beacon.origin_id,  # Keep origin ID
            hop_limit=original_beacon.hop_limit - 1  # Only decrement hop limit
        )