import time
import heapq
import traceback
from typing import Optional
import random
from utils.metrics import Metrics
from buoys.buoy import Buoy
from core.channel import Channel
from core.events import EventType, Event
from config.config_handler import ConfigHandler
from utils import logging
from scipy.spatial import cKDTree

# Sim-time delay before the first buoy-array update in the ramp/random scenarios
FIRST_ARRAY_UPDATE_DELAY: float = 30.0

# Constants for metrics sampling intervals based on the scenario type
SAMPLE_INTERVAL_RAMP: float = 5.0
SAMPLE_INTERVAL_OTHER: float = 16.0

# Constants for random buoy array update intervals in the random scenario
MIN_INTERVAL_RANDOM: float = 15.0
MAX_INTERVAL_RANDOM: float = 20.0

class Simulator:
    def __init__(self, buoys: list[Buoy], channel: Channel, metrics: Metrics, scenario: str, duration: float):
        cfg = ConfigHandler()
        
        # Simulation main components & settings
        self.scenario: str          = scenario
        self.buoys: list[Buoy]      = buoys
        self.channel: Channel       = channel
        self.metrics: Metrics       = metrics

        # When on ramp scenario only the first 2 buoys are considered active (existing in the network)
        if self.scenario == "ramp":
            for b in self.buoys[2:]:
                b.deactivate() # python annotation
            self._active_count: int = 2

            # Fixed interval between successive buoy activations (spread over the simulation period)
            buoys_to_add: int             = len(self.buoys) - 2
            ramp_window: float            = max(duration - FIRST_ARRAY_UPDATE_DELAY, 0.0)
            self.ramp_add_interval: float = (ramp_window / buoys_to_add) if buoys_to_add > 0 else duration
        
        # Otherwise all buoys are considered active at the start of the simulation
        else:
            self._active_count: int = len(self.buoys)

        # Duration time of the simulation in seconds
        self.duration: float = duration
        
        # Neighbor settings
        self.neighbor_timeout: float = cfg.get('scheduler', 'neighbor_timeout')
        self.comm_range_max: float   = cfg.get('network', 'communication_range_max')

        # Random scenario settings & precompute constants that never change
        self.random_variability: float = cfg.get('simulation', 'random_variability')
        total_buoys: int               = len(self.buoys)
        self.rnd_max_change: int       = max(1, int(total_buoys * self.random_variability))
        self.rnd_min_buoys: int        = max(3, int(total_buoys * 0.2))

        # Channel settings
        self.channel.set_buoys(self.buoys)
        self.channel.schedule_callback = self.schedule_event
        
        # Setting callback function for buoys to schedule their events
        for buoy in self.buoys:
            buoy.schedule_callback = self.schedule_event

        # Event variables for managing the event queue
        self.event_queue: list  = []
        self.event_counter: int = 0

    # Scheduler of events ordered by their scheduled time (counter breaks ties in FIFO order)
    def schedule_event(self, time: float, event_type: EventType, target_obj: Buoy | Channel, data: Optional[dict] = None):
        event = Event(time, event_type, target_obj, data)
        self.event_counter += 1
        heapq.heappush(self.event_queue, (event.time, self.event_counter, event))
    
    # Retrieves the next event from the event queue
    def _get_next_event(self) -> Optional[Event]:
        if not self.event_queue:
            return None
        _, _, event = heapq.heappop(self.event_queue)
        return event
    
    # Dispatches the event to the appropriate handler
    def handle_event(self, event: Event, sim_time: float):
        match event.event_type:
            case EventType.BUOY_ARRAY_UPDATE:
                self.update_buoy_array(sim_time)
            case EventType.AVG_NEIGHBORS_CALCULATION:
                self._sample_metrics(sim_time)
                sample_interval = SAMPLE_INTERVAL_RAMP if self.scenario == "ramp" else SAMPLE_INTERVAL_OTHER
                self.schedule_event(sim_time + sample_interval, EventType.AVG_NEIGHBORS_CALCULATION, self)
            case _:
                logging.log_error(f"Simulator received unhandled event: {event.event_type}")

    # Schedules the recurring events for an active buoy: scheduler check, neighbor cleanup and movement (for mobile ones)
    def _schedule_buoy_events(self, buoy, base_time: float = 0.0):
        initial_offset = random.uniform(0, 1.0)
        gen = {'_gen': buoy._generation}

        # Trigger periodic events chain
        self.schedule_event(base_time + initial_offset, EventType.SCHEDULER_CHECK, buoy, gen)
        self.schedule_event(base_time + initial_offset + self.neighbor_timeout, EventType.NEIGHBOR_CLEANUP, buoy, gen)

        # Mobile buoys also update their position
        if buoy.is_mobile:
            self.schedule_event(base_time + initial_offset, EventType.BUOY_MOVEMENT, buoy, gen)

    # Schedules initial events for buoys, channel and simulator itself
    def _schedule_initial_events(self):
        for buoy in self.buoys:
            # If the buoy is not active at the start of the simulation, it skips scheduling its events
            if not buoy.active:
                continue

            self._schedule_buoy_events(buoy)

        # Periodic metrics sampling runs in every scenario: SAMPLE_INTERVAL_RAMP (5s) for
        # ramp (timepoint logs), SAMPLE_INTERVAL_OTHER (16s) otherwise. Mobile buoys move
        # even in the static scenario, so the average-neighbor count must be resampled
        # there too (not just once at t=0).
        if self.metrics:
            sample_interval = SAMPLE_INTERVAL_RAMP if self.scenario == "ramp" else SAMPLE_INTERVAL_OTHER
            self.schedule_event(sample_interval, EventType.AVG_NEIGHBORS_CALCULATION, self)

        # Buoy array updates only happen in the dynamic scenarios (ramp/random)
        if self.scenario == "static":
            return

        # Schedule first buoy array update for dynamic scenarios (ramp/random)
        self.schedule_event(FIRST_ARRAY_UPDATE_DELAY, EventType.BUOY_ARRAY_UPDATE, self)

    # Updates the buoy array based on the scenario type (ramp/random)
    def update_buoy_array(self, sim_time: float):
        match self.scenario:
            case "ramp":
                self._update_buoy_array_ramp(sim_time)
            case "random":
                self._update_buoy_array_random(sim_time)
    
    # Updates the buoy array in ramp scenario by activating one buoy at a time until they are all active
    def _update_buoy_array_ramp(self, sim_time: float):
        # If buoys are all active then stop adding more
        total_buoys = len(self.buoys)
        if self._active_count >= total_buoys:
            return

        # With next() it finds the first inactive buoy without allocating a list
        buoy = next(b for b in self.buoys if not b.active)
        buoy.activate()
        self._active_count += 1

        # Scheduling initial events for the newly added buoy
        self._schedule_buoy_events(buoy, sim_time)
        self.schedule_event(sim_time + self.ramp_add_interval, EventType.BUOY_ARRAY_UPDATE, self)
   
    # Updates the buoy array in random scenario by randomly activating or deactivating buoys based on defined probabilities and constraints
    def _update_buoy_array_random(self, sim_time: float):
        # If there are more than minimum number of buoys active, randomly decide to remove some buoys with a 50% chance
        if self._active_count > self.rnd_min_buoys and random.random() < 0.5:
            active_buoys = [b for b in self.buoys if b.active]
            num_to_remove = random.randint(1, min(self.rnd_max_change, self._active_count - self.rnd_min_buoys))

            for buoy in random.sample(active_buoys, num_to_remove):
                buoy.deactivate()
            
            self._active_count -= num_to_remove
            logging.log_info(f"Removed {num_to_remove} buoys, now {self._active_count} active at {sim_time:.2f}s")

        # Otherwise activate some inactive buoys
        else:
            inactive_buoys = [b for b in self.buoys if not b.active]
            if inactive_buoys:
                num_to_add = random.randint(1, min(self.rnd_max_change, len(inactive_buoys)))

                for buoy in random.sample(inactive_buoys, num_to_add):
                    buoy.activate()
                    self._schedule_buoy_events(buoy, sim_time)

                self._active_count += num_to_add
                logging.log_info(f"Added {num_to_add} buoys, now {self._active_count} active at {sim_time:.2f}s")

        random_interval = random.uniform(MIN_INTERVAL_RANDOM, MAX_INTERVAL_RANDOM)
        self.schedule_event(sim_time + random_interval, EventType.BUOY_ARRAY_UPDATE, self)

    # Starts the simulation loop, processing events until the simulation duration ends or gets interrupted
    def start(self) -> float:
        # Simulation state variables
        last_time_log: float    = None
        real_time_start: float  = time.time()
        running: bool           = True
        simulated_time: float   = 0.0
        
        logging.reset() # Resetting logs at the start of the simulation

        # Initial metrics sample and schedule initial events
        self._sample_metrics(simulated_time)
        self._schedule_initial_events()
        
        try:
            # Main simulation loop: process events until the simulation time exceeds the duration or there are no more events
            while running and simulated_time < self.duration:
                event: Optional[Event] = self._get_next_event()
                if not event:
                    logging.log_info("No more events to process.")
                    break
                
                # Update simulated time to the time of the event being processed
                simulated_time = event.time

                # Per-event log bookkeeping is gated on one flag check
                if logging.LOGGING_ENABLED:
                    if event.event_type in (EventType.TRANSMISSION_START, EventType.RECEPTION):
                        logging.log_info(f"Processing {event.event_type.name} event")

                    time_log = int(simulated_time)
                    if last_time_log != time_log and time_log > 0 and time_log % 10 == 0:
                        logging.log_info(f"Time: {simulated_time:.2f}s, Event queue size: {len(self.event_queue)}")
                        last_time_log = time_log

                # Handle the event and catch any exceptions to prevent the simulation from crashing
                try:
                    event.target_obj.handle_event(event, simulated_time)
                except Exception:
                    logging.log_error(
                        f"Error handling {event.event_type.name} event:\n{traceback.format_exc()}"
                    )

        except KeyboardInterrupt:
            logging.log_info("Simulation interrupted by user.")
            running = False
            
        # Final log with simulation results and performance metrics
        real_time_end = time.time()
        real_duration = real_time_end - real_time_start
        sim_speedup = simulated_time / real_duration if real_duration > 0 else float('inf')
        logging.log_info(f"Simulation complete. {simulated_time:.2f}s simulated in {real_duration:.2f}s real time (speedup: {sim_speedup:.2f}x)")
        
        return simulated_time

    # Calculates the average number of neighbors for the current buoy array (O(n log n) using k-d tree)
    def calculate_avg_neighbors(self) -> float: 
        if not self._active_count:
            return 0.0

        # Build the k-d tree using only active buoys' positions
        points = [b.position for b in self.buoys if b.active]
        tree = cKDTree(points)

        # Finds all unique pairs of buoys within communication range
        # Each pair (i, j) counts as two directed neighbor relationships
        pairs = tree.query_pairs(self.comm_range_max)
        return (len(pairs) * 2) / self._active_count
    
    # Samples avg_neighbors once and dispatches to metrics sinks (sample always; timepoint log for ramp)
    def _sample_metrics(self, sim_time: float):
        if not self.metrics:
            return

        avg_neighbors: float = self.calculate_avg_neighbors()
        self.metrics.record_avg_neighbors_sample(avg_neighbors)
        if self.scenario == "ramp":
            self.metrics.log_timepoint(sim_time, self._active_count, avg_neighbors)