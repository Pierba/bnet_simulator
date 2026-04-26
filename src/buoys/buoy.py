import uuid
import random
import math
from enum import Enum
from typing import Tuple, List, Dict

from protocols.scheduler import BeaconScheduler
from protocols.beacon import Beacon
from core.events import EventType, Event
from config.config_handler import ConfigHandler
from utils import logging
from core.channel import Channel

class BuoyState(Enum):
    SLEEPING = 0
    RECEIVING = 1
    WAITING_DIFS = 2
    BACKOFF = 3

class Buoy:
    # Initialization of a buoy with its properties
    def __init__(
        self,
        channel: Channel,
        position: Tuple[float, float] = (0.0, 0.0),
        is_mobile: bool = False,
        battery: float = None,
        velocity: Tuple[float, float] = (0.0, 0.0),
        metrics = None
    ):
        cfg = ConfigHandler()
        
        # Buoy properties
        self.id: uuid.UUID = uuid.uuid4()
        self.position: Tuple[float, float] = position
        self.is_mobile: bool = is_mobile
        self.battery: float = battery if battery is not None else cfg.get('buoys', 'default_battery')
        self.velocity: Tuple[float, float] = velocity
        self.neighbors: Dict[uuid.UUID, Tuple[uuid.UUID, float, Tuple[float, float]]] = {}  # Direct neighbors (1-hop, beacons we received directly)
        self.scheduler: BeaconScheduler = BeaconScheduler()
        self.channel: Channel = channel
        self.state: BuoyState = BuoyState.RECEIVING # Default state is RECEIVING
        self.metrics: dict = metrics
        self.schedule_callback: callable = None # To be set by simulator for event scheduling

        # CSMA parameters
        self.difs_time: float = cfg.get('csma', 'difs_time')
        self.slot_time: float = cfg.get('csma', 'slot_time')
        self.cw: int = cfg.get('csma', 'cw')

        # Network parameters for distance calculations
        self.neighbor_timeout: float = cfg.get('scheduler', 'neighbor_timeout')
        self.world_width: float = cfg.get('world', 'width')
        self.world_height: float = cfg.get('world', 'height')
        self.speed_of_light: float = cfg.get('network', 'speed_of_light')
        self.comm_range_max: float = cfg.get('network', 'communication_range_max')
        self.comm_range_max_sq: float = self.comm_range_max * self.comm_range_max

        # Multihop mode configuration
        self.multihop_mode: bool = cfg.get('simulation', 'multihop_mode')
        self.multihop_limit: int = cfg.get('simulation', 'multihop_limit')

        # State variables for CSMA and scheduling
        self.backoff_remaining: float = 0.0
        self.next_try_time: float = 0.0
        self.want_to_send: bool = False
        self.scheduler_decision_time: float = 0.0
        self.pending_forward_beacon = None  # Beacon queued for forwarding through CSMA

        # Multihop append mode: store discovered nodes (node_id, timestamp, position)
        # These are nodes we learned about from other beacons' neighbor lists
        self.discovered_nodes: Dict[uuid.UUID, Tuple[uuid.UUID, float, Tuple[float, float]]] = {}
        
        # Multihop forwarded mode: track seen beacons to avoid forwarding duplicates
        self.forwarded_beacons: Dict[Tuple[uuid.UUID, float], float] = {}
        
    def handle_event(self, event: EventType, sim_time: float):
        # Dispatch event to the appropriate handler based on event type
        handlers = {
            EventType.SCHEDULER_CHECK:      self._handle_scheduler_check,
            EventType.CHANNEL_SENSE:        self._handle_channel_sense,
            EventType.DIFS_COMPLETION:      self._handle_difs_completion,
            EventType.BACKOFF_SLOT:         self._handle_backoff_slot,
            EventType.TRANSMISSION_START:   self._handle_transmission_start,
            EventType.RECEPTION:            self._handle_reception,
            EventType.NEIGHBOR_CLEANUP:     self._handle_neighbor_cleanup,
            EventType.BUOY_MOVEMENT:        self._handle_buoy_movement
        }
        
        handler = handlers.get(event.event_type)
        if not handler:
            logging.log_error(f"Buoy {str(self.id)[:6]} received unhandled event: {event.event_type}")
            return
                
        handler(event, sim_time)

    # Scheduler check handler: asks the scheduler if we should send a beacon and schedules next check
    def _handle_scheduler_check(self, event: Event, sim_time: float):
        # Ask the scheduler if we should send a beacon based on current conditions
        neighbor_timestamps = [data[1] for data in self.neighbors.values()]
        should_send = self.scheduler.should_send(
            self.battery, self.velocity, neighbor_timestamps, sim_time
        )
        
        # If scheduler decides we should send, set want_to_send and schedule a channel sense to attempt transmission
        if should_send:
            self.want_to_send = True
            self.scheduler_decision_time = sim_time
            self.schedule_callback(
                sim_time, EventType.CHANNEL_SENSE, self
            )
        
        # Schedule the next scheduler check
        next_check_interval = self.scheduler.get_next_check_interval()
        self.schedule_callback(
            sim_time + next_check_interval, EventType.SCHEDULER_CHECK, self
        )

    # Channel sense handler: checks if the channel is busy and either schedules a retry or proceeds with DIFS/backoff
    def _handle_channel_sense(self, event: Event, sim_time: float):
        # Check if this is a forwarding request and in case it is queue it 
        forward_beacon = event.data.get("forward_beacon")
        if forward_beacon:
            self.pending_forward_beacon = forward_beacon
    
        # If it is ready to send or there is a forwarded beacon to send start the CSMA processs
        if self.want_to_send or self.pending_forward_beacon:
            if self.channel.is_busy(self.position, sim_time):
                # Channel is busy => wait one slot and check again
                self.schedule_callback(
                    sim_time + self.slot_time, EventType.CHANNEL_SENSE, self
                )
            else:
                # Channel is idle => proceed with DIFS and backoff as needed
                self.state = BuoyState.WAITING_DIFS
                # Simulate DIFS delay before checking channel again for backoff decision
                self.schedule_callback(
                    sim_time + self.difs_time, EventType.DIFS_COMPLETION, self
                )

    # DIFS completion handler: after DIFS time completition, checks channel again and either transmit immediately or enter backoff
    def _handle_difs_completion(self, event: Event, sim_time: float):
        # If the buoy no longer wants to send/forward or state has changed, do nothing
        if not(self.want_to_send or self.pending_forward_beacon) or self.state != BuoyState.WAITING_DIFS:
            return
            
        # After DIFS, check channel again to decide if we can transmit immediately or need to backoff
        if self.channel.is_busy(self.position, sim_time):
            self.state = BuoyState.RECEIVING
            self.schedule_callback(sim_time, EventType.CHANNEL_SENSE, self)
            return    

        # Only generate new random backoff if we don't have remaining backoff
        if self.backoff_remaining <= 0:
            backoff_slots = random.randint(0, self.cw - 1)
            self.backoff_remaining = backoff_slots * self.slot_time
            # If backoff is zero, we can transmit immediately without waiting for a slot
            if self.backoff_remaining <= 0:
                self.schedule_callback(sim_time, EventType.TRANSMISSION_START, self)
                return

        # Start or resume slot-by-slot backoff countdown
        self.state = BuoyState.BACKOFF     
        self.schedule_callback(
            sim_time + self.slot_time, 
            EventType.BACKOFF_SLOT, 
            self
        )

    # Backoff slot handler: checks channel status each slot and either decrements backoff or transmits if backoff is complete
    def _handle_backoff_slot(self, event: Event, sim_time: float):
        if not(self.want_to_send or self.pending_forward_beacon) or self.state != BuoyState.BACKOFF:
            return
            
        if self.channel.is_busy(self.position, sim_time):
            # Channel is busy then pause backoff and wait for channel to be clean before resuming
            self.state = BuoyState.RECEIVING
            self.schedule_callback(
                sim_time + self.slot_time, EventType.CHANNEL_SENSE, self
            )
            return

        # When channel is idle one slot of backoff gets decremented
        self.backoff_remaining -= self.slot_time
        if self.backoff_remaining <= 0:
            # If Backoff time completed successfully then transmit
            self.schedule_callback(
                sim_time, EventType.TRANSMISSION_START, self
            )
        else:
            # Otherwise more slots are remaining
            self.schedule_callback(
                sim_time + self.slot_time,
                EventType.BACKOFF_SLOT,
                self
            )

    # Transmission start handler: creates a beacon or forwards one and attempts to transmit it in broadcast through the channel
    def _handle_transmission_start(self, event: Event, sim_time: float):
        if not (self.want_to_send or self.pending_forward_beacon):
            return
        
        if self.pending_forward_beacon:
            # Forwarding a received beacon through CSMA
            forwarded = self.forward_beacon(self.pending_forward_beacon, sim_time)
            self.channel.broadcast(forwarded, sim_time)
            logging.log_info(f"Buoy {str(self.id)[:6]} forwarded beacon from {str(self.pending_forward_beacon.origin_id)[:6]}, hops left: {forwarded.hop_limit}")
            self.pending_forward_beacon = None
        else:
            # Normal beacon transmission
            beacon = self.create_beacon(sim_time)
            self.channel.broadcast(beacon, sim_time)
            self.want_to_send = False # Reset want_to_send flag after attempting transmission
            
            # Log transmission for latency tracking
            if self.metrics:
                latency = sim_time - self.scheduler_decision_time
                self.metrics.record_scheduler_latency(latency)
        
        self.backoff_remaining = 0.0  # Reset backoff for next transmission cycle
        self.state = BuoyState.RECEIVING

    def _handle_reception(self, event: Event, sim_time: float):
        beacon = event.data.get("beacon")
        if not beacon:
            return
  
        # Update direct neighbors of this buoy with the sender of the beacon (1-hop neighbor)
        self.neighbors[beacon.sender_id] = (beacon.sender_id, sim_time, beacon.position)
        
        # Multihop append mode: collect discovered nodes from beacon's neighbor list
        # These are NOT direct neighbors, but nodes we learned about indirectly
        if self.multihop_mode == 'append':
            for neighbor_id, neighbor_ts, neighbor_pos in beacon.neighbors:
                if neighbor_id == self.id or neighbor_id == beacon.sender_id:
                    continue
                
                # Update or add to discovered_nodes list with metadata
                # We always take the newest information (assuming neighbor_ts is useful or simply latest received)
                # However, we should only overwrite if the new timestamp is newer or we don't have it.
                if neighbor_id not in self.neighbors:
                    if neighbor_id not in self.discovered_nodes or neighbor_ts > self.discovered_nodes[neighbor_id][1]:
                        self.discovered_nodes[neighbor_id] = (neighbor_id, neighbor_ts, neighbor_pos)
        
        # Multihop forwarded mode: forward beacon WITHOUT modification if hop_limit > 0
        if self.multihop_mode == 'forwarded' and beacon.hop_limit > 0:
            beacon_key = (beacon.origin_id, beacon.timestamp)
            if beacon_key not in self.forwarded_beacons:
                self.forwarded_beacons[beacon_key] = sim_time
                # Schedule forwarding immediately
                self.schedule_callback(
                    sim_time + 0.001,
                    EventType.CHANNEL_SENSE,
                    self,
                    {"forward_beacon": beacon}
                )
        
        if self.metrics:
            # Track all unique nodes discovered from this beacon starting with the sender
            discovered_nodes = {beacon.sender_id}
            
            # In forward mode, also discover the origin if different
            if self.multihop_mode == 'forwarded' and beacon.origin_id is not None:
                if beacon.origin_id != self.id and beacon.origin_id != beacon.sender_id:
                    discovered_nodes.add(beacon.origin_id)
            
            # Discover all nodes from the beacon's neighbor list
            neighbor_ids = {neighbor_id for neighbor_id, _, _ in beacon.neighbors}
            discovered_nodes.update(neighbor_ids)
            discovered_nodes.discard(self.id)  # Don't count self as discovered

            # Track all unique nodes discovered from this beacon
            if self.id not in self.metrics.unique_nodes_per_buoy:
                self.metrics.unique_nodes_per_buoy[self.id] = set()
            self.metrics.unique_nodes_per_buoy[self.id].update(discovered_nodes)
            
            # Log reception for latency tracking
            self.metrics.log_received(
                sender_id=beacon.sender_id,
                timestamp=beacon.timestamp,
                receive_time=sim_time,
                receiver_id=self.id
            )
            
            # Track for delivery ratio
            self.metrics.log_actually_received(beacon.sender_id)

    def _handle_neighbor_cleanup(self, event, sim_time: float):
        # Cleanup direct neighbors
        self.neighbors = {
            nid: data for nid, data in self.neighbors.items()
            if sim_time - data[1] <= self.neighbor_timeout
        }
        
        # In append mode => cleanup old discovered nodes
        if self.multihop_mode == 'append':
            self.discovered_nodes = {
                nid: data for nid, data in self.discovered_nodes.items()
                if sim_time - data[1] <= self.neighbor_timeout
            }
            
        # In forwarded mode => cleanup old forwarded beacons
        if self.multihop_mode == 'forwarded':
            self.forwarded_beacons = {
                key: ts for key, ts in self.forwarded_beacons.items()
                if sim_time - ts <= self.neighbor_timeout
            }
        
        self.schedule_callback(
            sim_time + self.neighbor_timeout, EventType.NEIGHBOR_CLEANUP, self
        )

    def _handle_buoy_movement(self, event, sim_time: float):
        if not self.is_mobile:
            return
            
        dt = 0.1    # update too frequently
        x, y = self.position
        vx, vy = self.velocity
        
        new_x = x + vx * dt
        new_y = y + vy * dt
        
        # World boundries checks
        if new_x < 0:
            new_x = -new_x
            vx = -vx
        elif new_x > self.world_width:
            new_x = 2 * self.world_width - new_x
            vx = -vx
        
        if new_y < 0:
            new_y = -new_y
            vy = -vy
        elif new_y > self.world_height:
            new_y = 2 * self.world_height - new_y
            vy = -vy
            
        # Updating velocity and position
        self.velocity = (vx, vy)
        self.position = (new_x, new_y)
        
        # Schedule next movement update
        self.schedule_callback(
            sim_time + dt, EventType.BUOY_MOVEMENT, self
        )
    
    def create_beacon(self, sim_time: float) -> Beacon:
        all_neighbors = list(self.neighbors.values())
        
        # In append mode, add discovered nodes to the neighbor list
        # These are nodes learned from other beacons (not direct 1-hop neighbors)
        if self.multihop_mode == 'append':
            for node_id, data in self.discovered_nodes.items():
                if node_id not in self.neighbors:
                    all_neighbors.append(data)
        
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
            sender_id=self.id,                      # Forwarder becomes sender for transmission
            mobile=original_beacon.mobile,          # Keep original mobility
            position=self.position,                 # Use forwarder's position for range calculation
            battery=self.battery,                   # Forwarder's battery for transmission
            neighbors=original_beacon.neighbors,    # KEEP ORIGINAL NEIGHBORS - NO MODIFICATION
            timestamp=original_beacon.timestamp,    # Keep original timestamp
            origin_id=original_beacon.origin_id,    # Keep origin ID
            hop_limit=original_beacon.hop_limit - 1 # Only decrement hop limit
        )