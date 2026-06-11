import math
import random

from buoys.buoy import Buoy
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
        self.active_transmissions: list[tuple[Beacon, float, float]]    = []
        self.buoys: list[Buoy]                                          = []
        self.metrics: Metrics                                           = metrics
        self.schedule_callback: callable                                = None

        # Earliest time at which an active transmission expires; lets update() skip
        # rebuilding the list on the (very frequent) calls where nothing expired yet
        self._next_expiry: float = float('inf')
        self._buoys_by_id: dict = {}
        
        # Setting up network parameters from configuration
        self.ideal_channel: bool            = ideal_channel
        self.bit_rate: int                  = cfg.get('network', 'bit_rate')
        self.comm_range_max: float          = cfg.get('network', 'communication_range_max')
        self.comm_range_high_prob: float    = cfg.get('network', 'communication_range_high_prob')
        self.delivery_prob_high: float      = cfg.get('network', 'delivery_prob_high')
        self.delivery_prob_low: float       = cfg.get('network', 'delivery_prob_low')
        self.speed_of_light: float          = cfg.get('network', 'speed_of_light')
        
        # Precomputed values for efficiency
        self.comm_range_max_sq: float       = self.comm_range_max * self.comm_range_max
        self.comm_range_high_prob_sq: float = self.comm_range_high_prob * self.comm_range_high_prob
        self.grace_period                   = self.comm_range_max / self.speed_of_light + 1e-6

    # Setting the list of buoys in the channel, used for calculating receivers in range
    def set_buoys(self, buoys: list[Buoy]):
        self.buoys = buoys
        self._buoys_by_id = {buoy.id: buoy for buoy in buoys}

    def handle_event(self, event, sim_time: float): # Need to figure if deleting this handler ?
        match event.event_type:
            # case EventType.CHANNEL_UPDATE:
            #     self._handle_channel_update(event, sim_time)
            case EventType.TRANSMISSION_END:
                self._handle_transmission_end(event, sim_time)
            case _:
                logging.log_error(f"Channel received unhandled event: {event.event_type}")

    def _handle_channel_update(self, event, sim_time: float): # No more periodic updates, so this handler is not needed ?
        self.update(sim_time)
        # self.schedule_callback(
        #     sim_time + 1.0, EventType.CHANNEL_UPDATE, self  <--- no more periodic updates 
        # )

    # Logs the end of a transmission for debugging purposes
    def _handle_transmission_end(self, event, sim_time: float):
        if logging.LOGGING_ENABLED:
            beacon = event.data.get("beacon")
            if beacon:
                logging.log_info(f"Transmission completed at {sim_time} for beacon from {str(beacon.sender_id)[:6]}")

    # Keep only the transmissions that are still active based on sim_time
    def update(self, sim_time: float):
        # Fast path: the earliest expiry is still in the future, nothing to prune
        if sim_time < self._next_expiry:
            return

        grace = self.grace_period
        self.active_transmissions = [
            (beacon, start, end)
            for (beacon, start, end) in self.active_transmissions
            if end + grace > sim_time
        ]
        self._next_expiry = min(
            (end + grace for _, _, end in self.active_transmissions),
            default=float('inf')
        )

    def broadcast(self, beacon: Beacon, sim_time: float) -> float:
        if logging.LOGGING_ENABLED:
            logging.log_info(f"Broadcasting from {str(beacon.sender_id)[:6]} at {sim_time:.2f}s")

        # Update channel to remove expired transmissions before processing this new one
        self.update(sim_time)

        # Transmission time = bits / bit_rate
        transmission_time = beacon.size_bits() / self.bit_rate
        new_end_time = sim_time + transmission_time

        # Getting all potential receivers in range and their distances
        receivers_data = self._receivers_in_range(beacon)
        n_receivers = len(receivers_data)

        # Detect collisions with existing transmissions and identify which receivers are affected
        receivers_with_collisions, poisoned_count = self._detect_collisions(
            beacon, receivers_data, sim_time, new_end_time
        )

        # Record this transmission as active and schedule its end event
        self.active_transmissions.append((beacon, sim_time, new_end_time))
        expiry = new_end_time + self.grace_period
        if expiry < self._next_expiry:
            self._next_expiry = expiry
        self.schedule_callback(new_end_time, EventType.TRANSMISSION_END, self, {"beacon": beacon})

        # Schedule receptions for surviving receivers and count probabilistic losses if the channel is non-ideal
        probability_lost = self._schedule_receptions(
            beacon, receivers_data, receivers_with_collisions, new_end_time
        )

        # Logging and metrics recording for this transmission
        collision_lost = len(receivers_with_collisions)
        total_lost = collision_lost + probability_lost
        actual_successful = n_receivers - total_lost

        if logging.LOGGING_ENABLED:
            logging.log_info(f"Lost {total_lost} packets: {collision_lost} from collisions, {probability_lost} from probability")
            
        if self.metrics:
            is_forward = beacon.origin_id is not None and beacon.origin_id != beacon.sender_id
            self.metrics.log_sent(is_forward)
            self.metrics.log_potentially_sent(n_receivers)
            self.metrics.log_successful_receivers(actual_successful)
            self.metrics.log_collision(collision_lost)
            self.metrics.log_lost(total_lost)

            # Retroactive correction for earlier receptions revoked by this transmission
            if poisoned_count:
                self.metrics.log_collision(poisoned_count)
                self.metrics.log_lost(poisoned_count)
                self.metrics.log_successful_receivers(-poisoned_count)

        return new_end_time

    # Returns active buoys (excluding the sender) within communication range of the beacon
    def _receivers_in_range(self, beacon: Beacon) -> list[tuple[Buoy, float]]:
        receivers_data: list[tuple[Buoy, float]] = []
        beacon_x, beacon_y = beacon.position
        # Resolve the sender once so the per-buoy exclusion is an identity check
        # instead of a UUID comparison (which dominates this loop at scale)
        sender = self._buoys_by_id.get(beacon.sender_id)
        comm_range_sq = self.comm_range_max_sq

        for buoy in self.buoys:
            if buoy is sender or not buoy.active:
                continue

            bx, by = buoy.position
            dx, dy = bx - beacon_x, by - beacon_y
            dist_sq = (dx * dx) + (dy * dy)

            if dist_sq <= comm_range_sq:
                receivers_data.append((buoy, dist_sq))

        return receivers_data

    # Detects collisions between the new beacon and existing transmissions, identifying which receivers are affected
    def _detect_collisions(
            self, 
            beacon: Beacon, 
            receivers_data: list[tuple[Buoy, float]], 
            start_time: float, 
            end_time: float,
    ) -> tuple[set, int]:
        # Returns (receiver ids that lose this beacon, count of earlier receptions revoked)
        receivers_with_collisions = set()
        poisoned_count = 0

        # Concurrent transmissions are rare thanks to carrier sensing
        if not self.active_transmissions:
            return receivers_with_collisions, poisoned_count

        sender_id = beacon.sender_id
        beacon_x, beacon_y = beacon.position
        comm_range_sq = self.comm_range_max_sq

        # Receiver coordinates resolved once instead of per (transmission x receiver) pair
        receivers_pos = [(buoy.id, buoy.position[0], buoy.position[1]) for buoy, _ in receivers_data]

        for existing, start, end in self.active_transmissions:
            # Skip if this is the same sender
            if existing.sender_id == sender_id:
                continue

            # Skip transmissions whose time window does not overlap this one
            if not (start_time < end and start < end_time):
                continue

            # Two senders within range of each other => direct collision
            ex, ey = existing.position
            dx, dy = beacon_x - ex, beacon_y - ey
            senders_in_range = (dx * dx) + (dy * dy) <= comm_range_sq

            if senders_in_range and logging.LOGGING_ENABLED:
                logging.log_info(f"Direct collision between {str(sender_id)[:6]} and {str(existing.sender_id)[:6]}")

            for buoy_id, rx, ry in receivers_pos:
                # The new beacon is lost here on a direct collision, or whenever this
                # receiver also sits in range of the existing transmission's sender
                dx, dy = rx - ex, ry - ey
                hears_existing = (dx * dx) + (dy * dy) <= comm_range_sq

                if senders_in_range or hears_existing:
                    receivers_with_collisions.add(buoy_id)

                # Revoke the existing beacon's reception here only if one was actually
                # scheduled for this receiver -> poisoned_count counts real losses only
                if hears_existing and buoy_id in existing.scheduled_receivers:
                    existing.scheduled_receivers.discard(buoy_id)
                    poisoned_count += 1
                    if logging.LOGGING_ENABLED:
                        logging.log_info(f"Collision at receiver {str(buoy_id)[:6]} between {str(sender_id)[:6]} and {str(existing.sender_id)[:6]}")

        return receivers_with_collisions, poisoned_count
    
    # Schedules receptions for receivers that survived collisions and probabilistic loss
    def _schedule_receptions(
            self, 
            beacon: Beacon, 
            receivers_data: list[tuple[Buoy, float]], 
            receivers_with_collisions: set, 
            end_time: float,
    ) -> int:
        # Schedules a RECEPTION per surviving receiver; returns the probabilistic-loss count
        probability_lost = 0

        for buoy, dist_sq in receivers_data:
            # Skip receivers that already lost this beacon to a collision
            if buoy.id in receivers_with_collisions:
                continue

            # In a non-ideal channel, drop the packet with distance-dependent probability
            if not self.ideal_channel:
                delivery_prob = self.delivery_prob_high if dist_sq <= self.comm_range_high_prob_sq else self.delivery_prob_low
                if random.random() >= delivery_prob:
                    probability_lost += 1
                    continue

            # Accounting for propagation delay
            distance = math.sqrt(dist_sq)
            propagation_delay = distance / self.speed_of_light
            reception_time = end_time + propagation_delay + 1e-9

            # Mark this receiver as a valid recipient (a later colliding transmission can revoke it)
            beacon.scheduled_receivers.add(buoy.id)
            self.schedule_callback(reception_time, EventType.RECEPTION, buoy, {"beacon": beacon})

        return probability_lost

    # Check if the channel is actually busy at the given position and time
    def is_busy(self, position: tuple[float, float], sim_time: float) -> tuple[bool, float]:
        self.update(sim_time)

        busy = False
        next_free_time = sim_time
        px, py = position
        comm_range_sq = self.comm_range_max_sq
        speed_of_light = self.speed_of_light

        for beacon, start, end in self.active_transmissions:
            sx, sy = beacon.position
            dx, dy = px - sx, py - sy
            dist_sq = (dx * dx) + (dy * dy)

            # Skip if outside of detection range (squared compare avoids the sqrt)
            if dist_sq > comm_range_sq:
                continue

            # Calculate when the signal starts and stops passing through this specific position
            propagation_delay = math.sqrt(dist_sq) / speed_of_light

            arrival_time = start + propagation_delay
            cleared_time = end + propagation_delay

            # Channel is busy if the wave is currently over this position. Don't stop at
            # the first hit: keep the latest clear time so the caller retries only once.
            if arrival_time <= sim_time < cleared_time:
                busy = True
                if cleared_time > next_free_time:
                    next_free_time = cleared_time

        return busy, next_free_time