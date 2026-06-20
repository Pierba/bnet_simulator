from config.config_handler import ConfigHandler
from core.events import EventType, Event
from enum import Enum
import math
from protocols.beacon import Beacon
from protocols.scheduler import BeaconScheduler
import random
from utils import logging
import uuid

# Max random delay before a receiver forwards a beacon: desynchronizes
# the receivers of the same beacon, which would otherwise all start contending at once
FORWARD_JITTER_MAX: float = 0.05

class BuoyState(Enum):
    SLEEPING = 0
    RECEIVING = 1
    WAITING_DIFS = 2
    BACKOFF = 3

class Buoy:
    def __init__(
        self,
        scheduler: BeaconScheduler,
        position: tuple[float, float] = (0.0, 0.0),
        is_mobile: bool = False,
        velocity: tuple[float, float] = (0.0, 0.0),
        metrics: bool = False,
    ):
        cfg = ConfigHandler()

        # Buoy properties
        self.id: uuid.UUID                  = uuid.uuid4()
        self.scheduler: BeaconScheduler     = scheduler
        self.position: tuple[float, float]  = position
        self.is_mobile: bool                = is_mobile
        self.velocity: tuple[float, float]  = velocity
        self.metrics: bool                  = metrics

        # Network state
        self.active: bool           = True
        self._generation: int       = 0  # Incremented on each deactivation for lazy cancellation of scheduled events
        self.last_contact_ts: float = None
        self.state: BuoyState       = BuoyState.RECEIVING # Default state is RECEIVING
        self.neighbors: dict[uuid.UUID, tuple[float, tuple[float, float]]] = {}  # Direct neighbors (1-hop)
        
        # Callbacks to be set by the simulator for event scheduling, channel interactions and metrics tracking
        self.schedule_callback: callable                    = None
        self.channel_is_busy: callable                      = None
        self.channel_broadcast: callable                    = None
        self.record_scheduler_latency_callback: callable    = None
        self.set_unique_nodes_per_buoy_callback: callable   = None
        self.log_received_callback: callable                = None    

        # CSMA parameters
        self.cw: int                    = cfg.get('csma', 'cw')
        self.difs_time: float           = cfg.get('csma', 'difs_time')
        self.slot_time: float           = cfg.get('csma', 'slot_time')

        # Network parameters for distance calculations
        self.neighbor_timeout: float    = cfg.get('scheduler', 'neighbor_timeout')
        self.world_width: float         = cfg.get('world', 'width')
        self.world_height: float        = cfg.get('world', 'height')

        # Random Waypoint mobility model state
        # Speed is drawn uniformly from [rwp_speed_min, rwp_speed_max] per leg
        # Pause is drawn uniformly from [rwp_pause_min, rwp_pause_max] on waypoint arrival
        self.rwp_speed_min: float   = cfg.get('buoys', 'rwp_speed_min')
        self.rwp_speed_max: float   = cfg.get('buoys', 'rwp_speed_max')
        self.rwp_pause_min: float   = cfg.get('buoys', 'rwp_pause_min')
        self.rwp_pause_max: float   = cfg.get('buoys', 'rwp_pause_max')
        self.rwp_dt: float          = 0.5 # Time interval for movement updates in seconds

        # Randomly pick initial waypoint and speed when first spawned
        self.rwp_waypoint: tuple[float, float]  = self._pick_rwp_waypoint()
        self.rwp_speed: float                   = random.uniform(self.rwp_speed_min, self.rwp_speed_max)
        self.rwp_pause_until: float             = 0.0


        # State variables for CSMA and scheduling
        self.processing: bool               = False
        self.want_to_send: bool             = False
        self.scheduler_decision_time: float = 0.0

        # Multihop append mode: store discovered nodes from neighbor lists as
        # (last-contact ts, position, hop distance from this buoy)
        self.discovered_nodes: dict[uuid.UUID, tuple[float, tuple[float, float], int]] = {}
        
        # Multihop mode configuration ('none' | 'append' | 'forwarded')
        self.multihop_mode: str = cfg.get('simulation', 'multihop_mode')
        # Forwarded mode: multihop limit sets time to live for fowarded beacons
        self.multihop_limit: int = cfg.get('simulation', 'multihop_limit')
        # Append mode: hop limit for nodes to be included in the neighbor list (0 = unlimited, 1 = only direct neighbors, etc.)
        self.append_hop_limit: int = cfg.get('simulation', 'append_hop_limit')

        # Multihop forwarded mode: pending forwards are paced through the CSMA pipeline
        # one at a time, exactly like an own beacon. forwarded_beacons records the latest
        # timestamp decided per origin so duplicates aren't re-evaluated.
        self.pending_queue_limit: int                           = cfg.get('simulation', 'pending_queue_limit')
        self.forwarded_beacons: dict[uuid.UUID, float]          = {}
        self.pending_forward_beacons: dict[uuid.UUID, Beacon]   = {}

        # Event dispatch table for handling different event types with their corresponding methods
        self._event_handlers = {
            EventType.SCHEDULER_CHECK:              self._handle_scheduler_check,
            EventType.CHANNEL_SENSE:                self._handle_channel_sense,
            EventType.DIFS_COMPLETION:              self._handle_difs_completion,
            EventType.BACKOFF_COMPLETION:           self._handle_backoff_completion,
            EventType.TRANSMISSION_START:           self._handle_transmission_start,
            EventType.RECEPTION:                    self._handle_reception,
            EventType.NEIGHBOR_CLEANUP:             self._handle_neighbor_cleanup,
            EventType.BUOY_MOVEMENT:                self._handle_buoy_movement
        }

    def activate(self):
        self.active = True
        self.processing = False    # drop CSMA pipeline state left over from a prior cycle
        self.want_to_send = False

    def deactivate(self):
        self.active = False
        self._generation += 1      # invalidate recurring events scheduled in this cycle

        # A powered-down node loses its volatile forward queue: beacons are
        # time-sensitive advertisements, draining them after a downtime would
        # only burn channel time and skew latency metrics
        self.pending_forward_beacons.clear()

    # Schedules a generation-gated event targeting this buoy: events scheduled before a
    # deactivation are lazily discarded by handle_event once the buoy is reactivated
    def _schedule_event(self, time: float, event_type: EventType):
        self.schedule_callback(time, event_type, self, {'_gen': self._generation})

    def handle_event(self, event: EventType, sim_time: float):
        # Lazy cancellation: discard stale events for inactive buoys
        if not self.active:
            return

        # Discard recurring events that were scheduled before the last deactivation
        event_gen = event.data.get('_gen')
        if event_gen is not None and event_gen != self._generation:
            return

        # Dispatch event to the appropriate handler based on event type
        handler = self._event_handlers.get(event.event_type)
        if not handler:
            logging.log_error(f"Buoy {str(self.id)[:6]} received unhandled event: {event.event_type}")
            return

        # Call the handler for the event type
        handler(event, sim_time)

    # Scheduler check handler: asks the scheduler if we should send a beacon and schedules next check
    def _handle_scheduler_check(self, event: Event, sim_time: float):
        # Schedule the next scheduler check
        self._schedule_event(sim_time + self.scheduler.get_next_check_interval(), EventType.SCHEDULER_CHECK)

        # An own beacon is already queued for the current pipeline => don't re-roll the
        # scheduler decision (it would skew the send cadence and the latency metric)
        if self.want_to_send:
            return

        # Ask the scheduler whether an own beacon is due. This runs even while a forward
        # pipeline is draining, so the buoy's own advertisement is never starved by relay
        # traffic: the decision is recorded here and the in-flight pipeline (which always
        # prioritizes own beacons) sends it at its next contention.
        if not self.scheduler.should_send(self.velocity, len(self.neighbors), self.last_contact_ts, sim_time):
            return

        self.want_to_send = True
        self.scheduler_decision_time = sim_time

        # Start a fresh CSMA pipeline only if one isn't already in flight
        if not self.processing:
            self.processing = True
            self._schedule_event(sim_time, EventType.CHANNEL_SENSE)

    # Channel sense handler: checks if the channel is busy and either schedules a retry or proceeds with DIFS/backoff
    def _handle_channel_sense(self, event: Event, sim_time: float):
        # If it is ready to send or there is a forwarded beacon to send start the CSMA processs
        if not (self.want_to_send or self.pending_forward_beacons):
            return
        
        is_busy, next_try_time = self.channel_is_busy(self.position, sim_time)
        if is_busy:
            # Channel is busy => re-sense when it frees
            self._schedule_event(next_try_time, EventType.CHANNEL_SENSE)
            return

        # Channel is idle => proceed with DIFS and backoff as needed
        self.state = BuoyState.WAITING_DIFS
        
        # Simulate DIFS delay before checking channel again for backoff decision
        self._schedule_event(sim_time + self.difs_time, EventType.DIFS_COMPLETION)

    # DIFS completion handler: after DIFS time completion, checks channel again and either transmit immediately or enter backoff
    def _handle_difs_completion(self, event: Event, sim_time: float):
        # If the buoy no longer wants to send/forward or state has changed, do nothing
        if not(self.want_to_send or self.pending_forward_beacons):
            return

        # After DIFS, check channel again to decide if we can transmit immediately or need to backoff
        is_busy, next_try_time = self.channel_is_busy(self.position, sim_time)
        if is_busy:
            self.state = BuoyState.RECEIVING
            self._schedule_event(next_try_time, EventType.CHANNEL_SENSE)
            return

        # Draw a fresh backoff for this transmission attempt
        backoff_slots = random.randint(0, self.cw - 1) # Do we need to reset from DIFS and then resume backoff?
        backoff_remaining = backoff_slots * self.slot_time

        # Start or resume slot-by-slot backoff countdown => go over the entire backoff time
        self.state = BuoyState.BACKOFF
        self._schedule_event(sim_time + backoff_remaining, EventType.BACKOFF_COMPLETION)

    # Backoff slot handler: checks channel status each slot and either decrements backoff or transmits if backoff is complete
    def _handle_backoff_completion(self, event: Event, sim_time: float):
        if not(self.want_to_send or self.pending_forward_beacons):
            return
            
        is_busy, next_try_time = self.channel_is_busy(self.position, sim_time)
        if is_busy:
            # Busy during backoff => re-enter full CSMA (sense + DIFS + fresh backoff) instead of transmitting blindly
            self.state = BuoyState.RECEIVING
            self._schedule_event(next_try_time, EventType.CHANNEL_SENSE)
            return

        # If Backoff time completed successfully then transmit
        self._schedule_event(sim_time, EventType.TRANSMISSION_START)

    # Transmission start handler: sends a SINGLE beacon per CSMA win. Own beacons take
    # priority over forwards; if anything is still queued afterwards the buoy re-enters
    # the pipeline from scratch, so forwards are paced like ordinary traffic instead of
    # being drained back-to-back
    def _handle_transmission_start(self, event: Event, sim_time: float):
        if not (self.want_to_send or self.pending_forward_beacons):
            self.processing = False
            return

        self.state = BuoyState.RECEIVING

        # Send own beacon if the scheduler decided to, otherwise relay one queued forward
        if self.want_to_send:
            beacon = self.create_beacon(sim_time)
            self.channel_broadcast(beacon, sim_time)
            self.want_to_send = False

            if self.metrics:
                latency = sim_time - self.scheduler_decision_time
                self.record_scheduler_latency_callback(latency)
        else:
            self._transmit_forward_beacon(sim_time)

        # want_to_send is necessarily False here (an own beacon was either just sent and
        # cleared, or was never set). Any own beacon that becomes due later is picked up
        # by SCHEDULER_CHECK, so only a non-empty forward queue keeps the pipeline alive.
        if self.pending_forward_beacons:
            # Re-contend from scratch. The transmission just started holds the channel, so
            # the fresh sense backs off until it clears => full DIFS + backoff before the
            # next beacon goes out
            self._schedule_event(sim_time, EventType.CHANNEL_SENSE)
        else:
            self.processing = False

    # Relays a single pending beacon, discarding any stale entries it skips past
    def _transmit_forward_beacon(self, sim_time: float):
        # Take the first still-valid beacon from the queue (insertion-ordered dict)
        forward_beacon = None
        while self.pending_forward_beacons:
            origin_id, b = next(iter(self.pending_forward_beacons.items()))
            del self.pending_forward_beacons[origin_id]

            if sim_time - b.timestamp <= self.neighbor_timeout:
                forward_beacon = b
                break

        if not forward_beacon:
            return

        forwarded = self.forward_beacon(forward_beacon, sim_time)
        self.channel_broadcast(forwarded, sim_time)
        logging.log_info(f"Buoy {str(self.id)[:6]} forwarded beacon from {str(forward_beacon.origin_id)[:6]}, hops left: {forwarded.hop_limit}")

    # Reception handler: processes incoming beacon, updates neighbors and either appends discovered nodes or forwards the beacon
    def _handle_reception(self, event: Event, sim_time: float):
        beacon: Beacon = event.data.get("beacon")
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
                for neighbor_id, neighbor_ts, neighbor_pos, neighbor_hops in beacon.neighbors:
                    # This checks could be resolved with is operator ?
                    if neighbor_id == self.id or neighbor_id == beacon.sender_id: # This last case should not occur any time?
                        continue

                    # Skip the nodes that we already know as direct neighbors
                    if neighbor_id in self.neighbors:
                        continue

                    # This node sits one hop farther from us than from the beacon's sender.
                    # Nodes beyond the advertisement bound would never be re-advertised,
                    # so they are not stored at all (0 = unlimited)
                    hops = neighbor_hops + 1
                    if self.append_hop_limit and hops > self.append_hop_limit:
                        continue

                    # Update discovered nodes if beacon provides fresher information about this neighbor
                    if neighbor_ts > self.discovered_nodes.get(neighbor_id, (-1, None, 0))[0]:
                        self.discovered_nodes[neighbor_id] = (neighbor_ts, neighbor_pos, hops)

            # Multihop forwarded mode: queue beacon WITHOUT modification if hop_limit > 0.
            # Never re-forward a beacon this buoy originated (echo received via a neighbor)
            case 'forwarded' if beacon.hop_limit > 0 and beacon.origin_id != self.id:
                # Only act on a beacon fresher than the last one decided for this origin
                if beacon.timestamp > self.forwarded_beacons.get(beacon.origin_id, -1):
                    # Density-based forward suppression is decided once here, at enqueue
                    # time, so suppressed beacons never consume a CSMA contention. A
                    # suppressed beacon is still recorded as seen to dedupe later copies.
                    if not self.scheduler.should_forward(len(self.neighbors)):
                        self.forwarded_beacons[beacon.origin_id] = beacon.timestamp
                        logging.log_info(f"Buoy {str(self.id)[:6]} suppressed forward of {str(beacon.origin_id)[:6]}")

                    # Queue the beacon if it is already pending or there is room left
                    elif beacon.origin_id in self.pending_forward_beacons or len(self.pending_forward_beacons) < self.pending_queue_limit:
                        self.forwarded_beacons[beacon.origin_id] = beacon.timestamp
                        self.pending_forward_beacons[beacon.origin_id] = beacon

                        # Forwarding is paced like an own beacon: start a CSMA pipeline if
                        # one isn't already running. The jitter desynchronizes the many
                        # receivers of this beacon so they don't all contend at once.
                        if not self.processing:
                            self.processing = True
                            jitter = random.uniform(0, FORWARD_JITTER_MAX)
                            self._schedule_event(sim_time + jitter, EventType.CHANNEL_SENSE)

                    # Queue full => drop without recording, so a retry can land if room frees
                    else:
                        logging.log_info(f"Queue full, dropping beacon {str(beacon.origin_id)[:6]} from {str(beacon.sender_id)[:6]}")

        if self.metrics:
            # Track all unique nodes discovered from this beacon: its neighbor list,
            # the sender, and (in forward mode) the origin. Built as a single set,
            # this runs for every reception
            discovered_nodes = {neighbor_id for neighbor_id, _, _, _ in beacon.neighbors}
            discovered_nodes.add(beacon.sender_id)

            if self.multihop_mode == 'forwarded' and beacon.origin_id != beacon.sender_id:
                discovered_nodes.add(beacon.origin_id)

            discovered_nodes.discard(self.id)  # Don't count self as discovered

            # Track all unique nodes discovered from this beacon
            self.set_unique_nodes_per_buoy_callback(self.id, discovered_nodes)

            # Log the reception of this beacon, attributed to its origin: in forwarded
            # mode the sender is just the relay, while timestamp belongs to the origin
            self.log_received_callback(
                origin_id=beacon.origin_id or beacon.sender_id,
                timestamp=beacon.timestamp,
                receive_time=sim_time,
                receiver_id=self.id
            )

    def _handle_neighbor_cleanup(self, event: Event, sim_time: float):
        # Cleanup direct neighbors
        self.neighbors = {
            nid: data 
            for nid, data in self.neighbors.items()
            if sim_time - data[0] <= self.neighbor_timeout
        }
        
        match self.multihop_mode:
            # In append mode => cleanup old discovered nodes
            case 'append':
                self.discovered_nodes = {
                    nid: data 
                    for nid, data in self.discovered_nodes.items()
                    if sim_time - data[0] <= self.neighbor_timeout
                }

            # In forwarded mode => cleanup old forwarded beacons
            case 'forwarded':
                self.forwarded_beacons = {
                    key: ts 
                    for key, ts in self.forwarded_beacons.items()
                    if sim_time - ts <= self.neighbor_timeout
                }
            case _:
                pass
        
        # Schedule the next cleanup
        self._schedule_event(sim_time + self.neighbor_timeout, EventType.NEIGHBOR_CLEANUP)

    # Picks a random waypoint within the world boundaries
    def _pick_rwp_waypoint(self) -> tuple[float, float]:
        return (
            random.uniform(0.0, self.world_width),
            random.uniform(0.0, self.world_height)
        )

    # Handles buoy movement according to the Random Waypoint mobility model
    def _handle_buoy_movement(self, event: Event, sim_time: float):
        if not self.is_mobile:
            return

        # If the buoy is still paused at the previous waypoint it stays still and wakes up exactly when the pause expires
        if sim_time < self.rwp_pause_until:
            self.velocity = (0.0, 0.0)
            self._schedule_event(self.rwp_pause_until, EventType.BUOY_MOVEMENT)
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
            self._schedule_event(self.rwp_pause_until, EventType.BUOY_MOVEMENT)
            return

        # Moving toward waypoint
        nx, ny = dx / dist, dy / dist
        vx, vy = nx * self.rwp_speed, ny * self.rwp_speed

        # Update velocity and position
        self.velocity = (vx, vy)
        self.position = (x + vx * self.rwp_dt, y + vy * self.rwp_dt)

        # Schedule next movement
        self._schedule_event(sim_time + self.rwp_dt, EventType.BUOY_MOVEMENT)
    
    def create_beacon(self, sim_time: float) -> Beacon:
        # Beacon initialization parameters; direct neighbors sit at hop distance 1
        all_neighbors = [(id, ts, pos, 1) for id, (ts, pos) in self.neighbors.items()]
        origin_id = None
        hop_limit = 0

        match self.multihop_mode:
            # Add discovered nodes to the neighbor list
            case 'append':
                for node_id, (ts, pos, hops) in self.discovered_nodes.items():
                    if node_id not in self.neighbors: # Don't include direct neighbors again
                        all_neighbors.append((node_id, ts, pos, hops))
            
            # Set origin and hop limit
            case 'forwarded':
                origin_id = self.id
                hop_limit = self.multihop_limit
            
        return Beacon(
            sender_id=self.id,
            mobile=self.is_mobile,
            position=self.position,
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
            neighbors=original_beacon.neighbors,    # KEEP ORIGINAL NEIGHBORS - NO MODIFICATION
            timestamp=original_beacon.timestamp,    # Keep original timestamp

            origin_id=original_beacon.origin_id,        # Keep origin ID
            hop_limit=original_beacon.hop_limit - 1     # Only decrement hop limit
        )