import uuid
import random
import math
from enum import Enum

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
        position: tuple[float, float] = (0.0, 0.0),
        is_mobile: bool = False,
        battery: float = None,
        velocity: tuple[float, float] = (0.0, 0.0),
        metrics: bool = False
    ):
        cfg = ConfigHandler()
        
        # Buoy properties
        self.id: uuid.UUID = uuid.uuid4()
        self.position: tuple[float, float] = position
        self.is_mobile: bool = is_mobile
        self.battery: float = battery if battery is not None else cfg.get('buoys', 'default_battery')
        self.velocity: tuple[float, float] = velocity
        self.neighbors: dict[uuid.UUID, tuple[float, tuple[float, float]]] = {}  # Direct neighbors (1-hop, beacons we received directly)
        
        self.scheduler: BeaconScheduler = BeaconScheduler()
        self.last_contact_ts: float = None

        self.channel: Channel = channel
        self.state: BuoyState = BuoyState.RECEIVING # Default state is RECEIVING
        self.metrics: bool = metrics
        self.active: bool = True
        self._generation: int = 0  # Incremented on each deactivation; guards stale recurring events
        
        # Callbacks to be set by the simulator for event scheduling and metrics tracking
        self.schedule_callback: callable = None     
        self.record_scheduler_latency_callback: callable = None
        self.set_unique_nodes_per_buoy_callback: callable = None
        self.log_received_callback: callable = None    

        # CSMA parameters
        self.next_scheduler_time: float = 0.0  # Tracks when the next scheduler check is due
        self.difs_time: float = cfg.get('csma', 'difs_time')
        self.slot_time: float = cfg.get('csma', 'slot_time')
        self.cw: int = cfg.get('csma', 'cw')

        # Network parameters for distance calculations
        self.neighbor_timeout: float = cfg.get('scheduler', 'neighbor_timeout')
        self.world_width: float = cfg.get('world', 'width')
        self.world_height: float = cfg.get('world', 'height')
        self.comm_range_max: float = cfg.get('network', 'communication_range_max')
        self.comm_range_max_sq: float = self.comm_range_max * self.comm_range_max

        # Random Waypoint mobility model state
        # Speed is drawn uniformly from [rwp_speed_min, rwp_speed_max] per leg
        # Pause is drawn uniformly from [rwp_pause_min, rwp_pause_max] on waypoint arrival
        self.rwp_speed_min: float = cfg.get('buoys', 'rwp_speed_min')
        self.rwp_speed_max: float = cfg.get('buoys', 'rwp_speed_max')
        self.rwp_pause_min: float = cfg.get('buoys', 'rwp_pause_min')
        self.rwp_pause_max: float = cfg.get('buoys', 'rwp_pause_max')
        self.rwp_dt: float = 0.5  # Movement update interval (seconds)

        # Randomly pick initial waypoint and speed when first spawned
        self.rwp_waypoint: tuple[float, float] = self._pick_rwp_waypoint()
        self.rwp_speed: float = random.uniform(self.rwp_speed_min, self.rwp_speed_max)
        self.rwp_pause_until: float = 0.0  # sim_time before which this buoy is paused

        # Multihop mode configuration
        self.multihop_mode: bool = cfg.get('simulation', 'multihop_mode')
        self.multihop_limit: int = cfg.get('simulation', 'multihop_limit')

        # State variables for CSMA and scheduling
        # self.backoff_remaining: float = 0.0
        self.want_to_send: bool = False
        self.processing: bool = False
        self.scheduler_decision_time: float = 0.0

        # Multihop append mode: store discovered nodes (node_id, timestamp, position)
        # These are nodes we learned about from other beacons' neighbor lists
        self.discovered_nodes: dict[uuid.UUID, tuple[float, tuple[float, float]]] = {}
        
        # Multihop forwarded mode: track seen beacons to avoid forwarding duplicates
        # self.information_timeout: float = cfg.get('simulation', 'information_timeout')  # Time after which a beacon is considered outdated for forwarding decisions
        self.forwarded_beacons: dict[uuid.UUID, float] = {}
        self.pending_forward_beacons: dict[uuid.UUID, Beacon] = {}  # origin_id → Beacon, insertion-ordered FIFO
        self.pending_queue_limit: int = cfg.get('simulation', 'pending_queue_limit')

    def activate(self):
        self.active = True
        self.processing = False    # drop CSMA pipeline state left over from a prior cycle
        self.want_to_send = False

    def deactivate(self):
        self.active = False
        self._generation += 1      # invalidate recurring events scheduled in this cycle

    def handle_event(self, event: EventType, sim_time: float):
        # Lazy cancellation: discard stale events for inactive buoys in O(1)
        if not self.active:
            return

        # Discard recurring events that were scheduled before the last deactivation
        event_gen = event.data.get('_gen')
        if event_gen is not None and event_gen != self._generation:
            return

        # Dispatch event to the appropriate handler based on event type
        handlers = {
            EventType.SCHEDULER_CHECK:              self._handle_scheduler_check,
            EventType.CHANNEL_SENSE:                self._handle_channel_sense,
            EventType.DIFS_COMPLETION:              self._handle_difs_completion,
            EventType.BACKOFF_COMPLETITION:         self._handle_backoff_completition,
            EventType.TRANSMISSION_START:           self._handle_transmission_start,
            EventType.FORWARD_TRANSMISSION_START:   self._handle_forward_transmission,
            EventType.RECEPTION:                    self._handle_reception,
            EventType.NEIGHBOR_CLEANUP:             self._handle_neighbor_cleanup,
            EventType.BUOY_MOVEMENT:                self._handle_buoy_movement
        }
        
        handler = handlers.get(event.event_type)
        if not handler:
            logging.log_error(f"Buoy {str(self.id)[:6]} received unhandled event: {event.event_type}")
            return
                
        handler(event, sim_time)

    # Scheduler check handler: asks the scheduler if we should send a beacon and schedules next check
    def _handle_scheduler_check(self, event: Event, sim_time: float):
        # Schedule the next scheduler check
        self.next_scheduler_time = sim_time + self.scheduler.get_next_check_interval()
        self.schedule_callback(
            self.next_scheduler_time, EventType.SCHEDULER_CHECK, self, {'_gen': self._generation}
        )

        # Makes the transmission pipeline atomic by ignoring new scheduler decisions while processing a transmission
        if self.processing:
            return
        
        # Ask the scheduler if we should send a beacon based on current conditions
        n_neighbors = len(self.neighbors)
        self.want_to_send = self.scheduler.should_send(
            self.battery, self.velocity, n_neighbors, self.last_contact_ts, sim_time
        )
        
        # If scheduler decides we should send, start the CSMA pipeline for own beacon
        if self.want_to_send:
            self.processing = True
            self.scheduler_decision_time = sim_time
            self.schedule_callback(
                sim_time, EventType.CHANNEL_SENSE, self
            )
        elif self.pending_forward_beacons:
            # Scheduler doesn't need to send, but there are pending forwards to resume
            self.processing = True
            self.schedule_callback(
                sim_time, EventType.CHANNEL_SENSE, self
            )
            
    # Channel sense handler: checks if the channel is busy and either schedules a retry or proceeds with DIFS/backoff
    def _handle_channel_sense(self, event: Event, sim_time: float):
        # If it is ready to send or there is a forwarded beacon to send start the CSMA processs
        if not (self.want_to_send or self.pending_forward_beacons):
            return
        
        is_busy, next_try_time = self.channel.is_busy(self.position, sim_time)
        if is_busy:
            # Channel is busy => wait one slot and check again
            self.schedule_callback(
                # slot time best choice instead of waiting for the channel to be free
                next_try_time, EventType.CHANNEL_SENSE, self
            )
            return
        
        # Channel is idle => proceed with DIFS and backoff as needed
        self.state = BuoyState.WAITING_DIFS
        # Simulate DIFS delay before checking channel again for backoff decision
        self.schedule_callback(
            sim_time + self.difs_time, EventType.DIFS_COMPLETION, self
        )

    # DIFS completion handler: after DIFS time completition, checks channel again and either transmit immediately or enter backoff
    def _handle_difs_completion(self, event: Event, sim_time: float):
        # If the buoy no longer wants to send/forward or state has changed, do nothing
        if not(self.want_to_send or self.pending_forward_beacons): # Buoy state are useless for now
            return

        # After DIFS, check channel again to decide if we can transmit immediately or need to backoff
        is_busy, next_try_time = self.channel.is_busy(self.position, sim_time)
        if is_busy:
            self.state = BuoyState.RECEIVING
            self.schedule_callback(next_try_time, EventType.CHANNEL_SENSE, self)
            return    

        # Draw a fresh backoff for this transmission attempt
        backoff_slots = random.randint(0, self.cw - 1) # Do we need to reset from DIFS and then resume backoff?
        backoff_remaining = backoff_slots * self.slot_time

        # Start or resume slot-by-slot backoff countdown => go over the entire backoff time
        self.state = BuoyState.BACKOFF     
        self.schedule_callback(
            sim_time + backoff_remaining, EventType.BACKOFF_COMPLETITION, self
        )

    # Backoff slot handler: checks channel status each slot and either decrements backoff or transmits if backoff is complete
    def _handle_backoff_completition(self, event: Event, sim_time: float):
        if not(self.want_to_send or self.pending_forward_beacons):
            return
            
        is_busy, next_try_time = self.channel.is_busy(self.position, sim_time)
        if is_busy:
            # If channel is busy then we remain in this state until elegible for transmission start
            self.state = BuoyState.BACKOFF
            self.schedule_callback(
                next_try_time, EventType.BACKOFF_COMPLETITION, self
            )
            return

        # If Backoff time completed successfully then transmit
        self.schedule_callback(
            sim_time, EventType.TRANSMISSION_START, self
        )

    # Transmission start handler: sends own beacon and/or delegates forwarding
    def _handle_transmission_start(self, event: Event, sim_time: float):
        if not (self.want_to_send or self.pending_forward_beacons):
            self.processing = False
            return
        
        self.state = BuoyState.RECEIVING
        end_time = sim_time

        # Send own beacon if scheduler decided to send
        if self.want_to_send:
            beacon = self.create_beacon(sim_time)
            end_time = self.channel.broadcast(beacon, sim_time)
            self.want_to_send = False

            if self.metrics:
                latency = sim_time - self.scheduler_decision_time
                self.record_scheduler_latency_callback(latency)

        # Delegate forwarding: piggyback after own transmission or forward-only
        if self.pending_forward_beacons:
            self.schedule_callback(
                end_time, EventType.FORWARD_TRANSMISSION_START, self
            )
            return

        # Reset of processing state if there are no pending beacons
        self.processing = False

    # Forward transmission handler: carrier sense + drain loop for all pending forwards
    def _handle_forward_transmission(self, event: Event, sim_time: float):
        if not self.pending_forward_beacons:
            self.processing = False
            return

        # If the drain has reached the next scheduler check deadline, yield
        # so the scheduler can evaluate whether to send an own beacon first
        if sim_time >= self.next_scheduler_time:
            self.processing = False
            logging.log_info(f"Buoy {str(self.id)[:6]} yielded forwarding to scheduler at {sim_time:.4f}s")
            return  # Remaining forwards will be resumed by the next scheduler check

        # Checking each time if channel remains idle
        is_busy, next_try_time = self.channel.is_busy(self.position, sim_time)
        if is_busy:
            self.schedule_callback(next_try_time, EventType.FORWARD_TRANSMISSION_START, self)
            return

        # Take the first valid beacon from the queue (lazy evaluation, insertion-ordered dict)
        forward_beacon = None
        while self.pending_forward_beacons:
            origin_id, b = next(iter(self.pending_forward_beacons.items()))
            del self.pending_forward_beacons[origin_id]
            if sim_time - b.timestamp <= self.neighbor_timeout:
                forward_beacon = b
                break

        if not forward_beacon:
            self.processing = False
            return

        forwarded = self.forward_beacon(forward_beacon, sim_time)
        end_time = self.channel.broadcast(forwarded, sim_time)
        logging.log_info(f"Buoy {str(self.id)[:6]} forwarded beacon from {str(forward_beacon.origin_id)[:6]}, hops left: {forwarded.hop_limit}")

        if self.pending_forward_beacons:
            self.schedule_callback(end_time, EventType.FORWARD_TRANSMISSION_START, self)
            return
        
        self.processing = False

    def _handle_reception(self, event: Event, sim_time: float):
        beacon = event.data.get("beacon")
        if not beacon:
            return

        # Drop the beacon unless this receiver still holds a valid scheduled reception:
        # a later colliding transmission revokes it (a real radio sees corrupted bits)
        if self.id not in beacon.scheduled_receivers:
            return
  
        # Update direct neighbors of this buoy with the sender of the beacon (1-hop neighbors)
        self.neighbors[beacon.sender_id] = (sim_time, beacon.position)
        self.last_contact_ts = sim_time

        match self.multihop_mode:
            # Multihop append mode: collect discovered nodes from beacon's neighbor list
            # These are NOT direct neighbors, but nodes we learned about indirectly
            case 'append':
                for neighbor_id, neighbor_ts, neighbor_pos in beacon.neighbors:
                    if neighbor_id == self.id or neighbor_id == beacon.sender_id: # This last case should not occur any time?
                        continue
                    
                    # Skip the nodes that we already know as direct neighbors
                    if neighbor_id in self.neighbors:
                        continue
                    
                    # Update discovered nodes if beacon provides fresher information about this neighbor
                    if neighbor_ts > self.discovered_nodes.get(neighbor_id, (-1, None))[0]:
                        self.discovered_nodes[neighbor_id] = (neighbor_ts, neighbor_pos)

            # Multihop forwarded mode: forward beacon WITHOUT modification if hop_limit > 0
            case 'forwarded' if beacon.hop_limit > 0:
                if beacon.timestamp > self.forwarded_beacons.get(beacon.origin_id, -1):
                    if beacon.origin_id in self.pending_forward_beacons:
                        # Origin already queued: update in-place (preserves FIFO order, no size change)
                        self.forwarded_beacons[beacon.origin_id] = beacon.timestamp
                        self.pending_forward_beacons[beacon.origin_id] = beacon
                    elif len(self.pending_forward_beacons) >= self.pending_queue_limit:
                        logging.log_info(f"Queue full, dropping beacon {str(beacon.origin_id)[:6]} from {str(beacon.sender_id)[:6]}")
                    else:
                        self.forwarded_beacons[beacon.origin_id] = beacon.timestamp
                        self.pending_forward_beacons[beacon.origin_id] = beacon

                    # Start transmission pipeline if not active
                    if self.pending_forward_beacons and not self.processing:
                        self.processing = True
                        self.scheduler_decision_time = sim_time
                        self.schedule_callback(
                            sim_time, EventType.CHANNEL_SENSE, self
                        )
            case _:
                pass

        if self.metrics:
            # Track all unique nodes discovered from this beacon starting with the sender
            discovered_nodes = {beacon.sender_id}
            
            # In forward mode, also discover the origin if different
            if self.multihop_mode == 'forwarded':
                if beacon.origin_id != self.id and beacon.origin_id != beacon.sender_id:
                    discovered_nodes.add(beacon.origin_id)
            
            # Discover all nodes from the beacon's neighbor list
            neighbor_ids = {neighbor_id for neighbor_id, _, _ in beacon.neighbors}
            discovered_nodes.update(neighbor_ids)
            discovered_nodes.discard(self.id)  # Don't count self as discovered

            # Track all unique nodes discovered from this beacon
            self.set_unique_nodes_per_buoy_callback(self.id, discovered_nodes)

            self.log_received_callback(
                sender_id=beacon.sender_id,
                timestamp=beacon.timestamp,
                receive_time=sim_time,
                receiver_id=self.id
            )

    def _handle_neighbor_cleanup(self, event, sim_time: float):
        # Cleanup direct neighbors
        self.neighbors = {
            nid: data 
            for nid, data in self.neighbors.items()
            if sim_time - data[0] <= self.neighbor_timeout
        }
        
        match self.multihop_mode:
            case 'append':
                # In append mode => cleanup old discovered nodes
                self.discovered_nodes = {
                    nid: data 
                    for nid, data in self.discovered_nodes.items()
                    if sim_time - data[0] <= self.neighbor_timeout
                }
            case 'forwarded':
                # In forwarded mode => cleanup old forwarded beacons
                self.forwarded_beacons = {
                    key: ts 
                    for key, ts in self.forwarded_beacons.items()
                    if sim_time - ts <= self.neighbor_timeout
                }
            case _:
                pass
        
        self.schedule_callback(
            sim_time + self.neighbor_timeout, EventType.NEIGHBOR_CLEANUP, self,
            {'_gen': self._generation}
        )

    # Picks a random waypoint within the world boundaries
    def _pick_rwp_waypoint(self) -> tuple[float, float]:
        x = random.uniform(0.0, self.world_width)
        y = random.uniform(0.0, self.world_height)
        return (x, y)

    def _handle_buoy_movement(self, event, sim_time: float):
        if not self.is_mobile:
            return

        # If the buoy is still paused at the previous waypoint it stays still and wakes up exactly when the pause expires
        if sim_time < self.rwp_pause_until:
            self.velocity = (0.0, 0.0)
            self.schedule_callback(self.rwp_pause_until, EventType.BUOY_MOVEMENT, self, {'_gen': self._generation})
            return

        # Moving toward the waypoint
        x, y = self.position
        wx, wy = self.rwp_waypoint
        dx, dy = wx - x, wy - y
        dist = math.hypot(dx, dy)
        step = self.rwp_speed * self.rwp_dt

        if dist <= step:
            # The buoy reached its destination and stop itself
            self.position = self.rwp_waypoint
            self.velocity = (0.0, 0.0)

            # Calculating the pause time before next movement
            pause = random.uniform(self.rwp_pause_min, self.rwp_pause_max)
            self.rwp_pause_until = sim_time + pause

            # Picking a new waypoint and speed for the next movement
            self.rwp_waypoint = self._pick_rwp_waypoint()
            self.rwp_speed = random.uniform(self.rwp_speed_min, self.rwp_speed_max)

            logging.log_info(
                f"Buoy {str(self.id)[:6]} reached waypoint, pausing {pause:.2f}s, \
                    next waypoint ({self.rwp_waypoint[0]:.1f}, {self.rwp_waypoint[1]:.1f}) \
                    at speed {self.rwp_speed:.1f}"
            )

            # Resume after the pause
            self.schedule_callback(self.rwp_pause_until, EventType.BUOY_MOVEMENT, self, {'_gen': self._generation})
            return

        # Moving toward waypoint
        nx, ny = dx / dist, dy / dist
        vx, vy = nx * self.rwp_speed, ny * self.rwp_speed

        # Update velocity and position
        self.velocity = (vx, vy)
        self.position = (x + vx * self.rwp_dt, y + vy * self.rwp_dt)

        # Schedule next movement
        self.schedule_callback(sim_time + self.rwp_dt, EventType.BUOY_MOVEMENT, self, {'_gen': self._generation})
    
    def create_beacon(self, sim_time: float) -> Beacon:
        all_neighbors = [(id, ts, pos) for id, (ts, pos) in self.neighbors.items()]
        origin_id = None
        hop_limit = 0
        
        match self.multihop_mode:
            case 'append':
                # In append mode, add discovered nodes to the neighbor list
                # These are nodes learned from other beacons (not direct 1-hop neighbors)
                for node_id, (ts, pos) in self.discovered_nodes.items():
                    if node_id not in self.neighbors: # Don't include direct neighbors again
                        all_neighbors.append((node_id, ts, pos))
            case 'forwarded':
                # Set origin and hop_limit for forwarded mode
                origin_id = self.id
                hop_limit = self.multihop_limit
            case _:
                pass
        
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
            
            origin_id=original_beacon.origin_id,        # Keep origin ID
            hop_limit=original_beacon.hop_limit - 1     # Only decrement hop limit
        )