import time
import heapq
from typing import List, Dict, Optional
import random
from utils.metrics import Metrics
from buoys.buoy import Buoy
from core.channel import Channel
from core.events import EventType, Event
from config.config_handler import ConfigHandler
from utils import logging
from scipy.spatial import cKDTree

class Simulator:
    def __init__(self, buoys: List[Buoy], channel: Channel, metrics: Metrics, scenario: str, duration: float):
        cfg = ConfigHandler()
        
        self.scenario = scenario
        self.all_buoys: List[Buoy] = buoys
        self.channel: Channel = channel
        self.metrics: Metrics = metrics

        # When on ramp scenario only the first 2 buoys are considered active (existing in the network)
        if self.scenario == "ramp":
            for b in self.all_buoys[2:]:
                b.active = False
            self._active_count: int = 2
        else:
            self._active_count: int = len(self.all_buoys)

        # Durantion time of the simulation in seconds
        self.duration: float = duration
        
        # Neighbor settings
        self.neighbor_timeout: float = cfg.get('scheduler', 'neighbor_timeout')
        self.comm_range_max: float = cfg.get('network', 'communication_range_max')

        # Random scenario settings & precompute constants that never change
        self.random_variability: float = cfg.get('simulation', 'random_variability')
        total_buoys = len(self.all_buoys)
        self.rnd_min_buoys: int = max(3, int(total_buoys * 0.2))
        self.rnd_max_change: int = max(1, int(total_buoys * self.random_variability))

        # Channel settings
        self.channel.set_buoys(self.all_buoys)
        self.channel.schedule_callback = self.schedule_event
        self.running: bool = False
        self.simulated_time: float = 0.0
        self.last_timepoint_log: int = -1  # Track interval logged to avoid duplicate timepoints
        
        # Setting callback function for buoys to schedule their events
        for buoy in self.all_buoys:
            buoy.schedule_callback = self.schedule_event

        # Event variables for managing the event queue
        self.event_queue: list = []
        self.event_counter: int = 0

    # Scheduler of events ordered by their scheduled time
    def schedule_event(self, time: float, event_type: EventType, target_obj: Buoy | Channel, data: Optional[Dict] = None):
        event = Event(time, event_type, target_obj, data)
        self.event_counter += 1
        epsilon = self.event_counter * 1e-10
        heapq.heappush(self.event_queue, (event.time + epsilon, self.event_counter, event))
    
    # Retrieves the next event from the event queue
    def _get_next_event(self) -> Optional[Event]:
        if not self.event_queue:
            return None
        _, _, event = heapq.heappop(self.event_queue)
        return event

    # Schedule initial events for buoys, channel and simulator itself
    def _schedule_initial_events(self):
        for buoy in self.all_buoys:
            # If the buoy is not active at the start of the simulation, it skips scheduling its events.
            if not buoy.active:
                continue

            # Scheduling first events that will trigger themself periodically along the simulation
            initial_offset = random.uniform(0, 1.0)
            self.schedule_event(initial_offset, EventType.SCHEDULER_CHECK, buoy, {'_gen': buoy._generation})
            self.schedule_event(initial_offset + self.neighbor_timeout, EventType.NEIGHBOR_CLEANUP, buoy, {'_gen': buoy._generation})

            # Mobile buoys also update their position
            if buoy.is_mobile:
                self.schedule_event(initial_offset, EventType.BUOY_MOVEMENT, buoy, {'_gen': buoy._generation})
        
        # Schedule first buoy array update for dynamic scenarios
        if self.scenario == "static":
            return
        
        # Schedule first buoy array update for dynamic scenarios (ramp/random)
        self.schedule_event(30.0, EventType.BUOY_ARRAY_UPDATE, self)
        
        # Schedule periodic avg_neighbors calculation every 30 seconds if metrics are enabled
        if self.metrics:
            self.schedule_event(30.0, EventType.AVG_NEIGHBORS_CALCULATION, self)

    def update_buoy_array(self, sim_time: float):
        match self.scenario:
            case "ramp":
                self._update_buoy_array_ramp(sim_time)
            case "random":
                self._update_buoy_array_random(sim_time)

        if self.metrics:
            # Recalculate avg_neighbors after buoy array changes
            self.calculate_and_record_avg_neighbors()

    def handle_event(self, event: Event, sim_time: float):
        match event.event_type:
            case EventType.BUOY_ARRAY_UPDATE:
                self.update_buoy_array(sim_time)
            case EventType.AVG_NEIGHBORS_CALCULATION:
                self.calculate_and_record_avg_neighbors()
                self.schedule_event(sim_time + 30.0, EventType.AVG_NEIGHBORS_CALCULATION, self)
            case _:
                logging.log_error(f"Simulator received unhandled event: {event.event_type}")
    
    def _update_buoy_array_ramp(self, sim_time: float):
        # If buoys are all active then stop adding more
        total_buoys = len(self.all_buoys)
        if self._active_count >= total_buoys:
            return

        # Calculate the interval at which to add buoys based on the total number of buoys and the simulation duration
        buoys_to_add = total_buoys - 2
        add_interval = (self.duration / buoys_to_add) if buoys_to_add > 0 else self.duration

        # With next() it finds the first inactive buoy without allocating a list
        buoy = next(b for b in self.all_buoys if not b.active)
        buoy.active = True
        self._active_count += 1

        # Scheduling initial events for the newly added buoy
        initial_offset = random.uniform(0, 1.0)
        self.schedule_event(sim_time + initial_offset, EventType.SCHEDULER_CHECK, buoy, {'_gen': buoy._generation})
        self.schedule_event(sim_time + initial_offset + self.neighbor_timeout, EventType.NEIGHBOR_CLEANUP, buoy, {'_gen': buoy._generation})
        self.schedule_event(sim_time + add_interval, EventType.BUOY_ARRAY_UPDATE, self)
   
    def _update_buoy_array_random(self, sim_time: float):
        # If there are more than minimum number of buoys active, randomly decide to remove some buoys with a 50% chance
        if self._active_count > self.rnd_min_buoys and random.random() < 0.5:
            active_buoys = [b for b in self.all_buoys if b.active]
            num_to_remove = random.randint(1, min(self.rnd_max_change, self._active_count - self.rnd_min_buoys))
            for buoy in random.sample(active_buoys, num_to_remove):
                buoy.active = False
            self._active_count -= num_to_remove
            logging.log_info(f"Removed {num_to_remove} buoys, now {self._active_count} active at {sim_time:.2f}s")

        # Otherwise deactivate some random buoys
        else:
            inactive_buoys = [b for b in self.all_buoys if not b.active]
            if inactive_buoys:
                num_to_add = random.randint(1, min(self.rnd_max_change, len(inactive_buoys)))

                for buoy in random.sample(inactive_buoys, num_to_add):
                    buoy.active = True
                    
                    initial_offset = random.uniform(0, 1.0)
                    self.schedule_event(sim_time + initial_offset, EventType.SCHEDULER_CHECK, buoy, {'_gen': buoy._generation})
                    self.schedule_event(sim_time + initial_offset + self.neighbor_timeout, EventType.NEIGHBOR_CLEANUP, buoy, {'_gen': buoy._generation})

                    if buoy.is_mobile:
                        self.schedule_event(sim_time + initial_offset, EventType.BUOY_MOVEMENT, buoy, {'_gen': buoy._generation})
                
                self._active_count += num_to_add
                logging.log_info(f"Added {num_to_add} buoys, now {self._active_count} active at {sim_time:.2f}s")

        self.schedule_event(sim_time + random.uniform(15, 20), EventType.BUOY_ARRAY_UPDATE, self)

    def start(self):
        self.running = True
        real_time_start = time.time()
        logging.reset() # Resetting metrics and logs at the start of the simulation

        # Calculate initial avg_neighbors and schedule initial events
        self.calculate_and_record_avg_neighbors()
        self._schedule_initial_events()

        last_time_log = None
        try:
            # Main simulation loop: process events until the simulation time exceeds the duration or there are no more events
            while self.running and self.simulated_time < self.duration:
                event: Optional[Event] = self._get_next_event()
                if not event:
                    logging.log_info("No more events to process.")
                    break
                
                # Update simulated time to the time of the event being processed
                self.simulated_time = event.time
                
                if event.event_type in [EventType.TRANSMISSION_START, EventType.RECEPTION]:
                    logging.log_info(f"Processing {event.event_type.name} event")
                # logging.log_info(f"Processing {event.event_type.name} event")
                
                time_log = int(self.simulated_time)
                if last_time_log != time_log and time_log > 0 and time_log % 10 == 0:
                    logging.log_info(f"Time: {self.simulated_time:.2f}s, Event queue size: {len(self.event_queue)}")
                    last_time_log = time_log

                # Handle the event and catch any exceptions to prevent the simulation from crashing
                try:
                    event.target_obj.handle_event(event, self.simulated_time)
                except Exception as e:
                    logging.log_error(f"Error handling event {event}: {str(e)}")
                
                if self.scenario == "ramp" and self.simulated_time > 0:
                    current_interval = int(self.simulated_time) // 5 # Should it be this the interval?
                    if current_interval != self.last_timepoint_log:
                        self.last_timepoint_log = current_interval
                        avg_neighbors_sample = self.calculate_avg_neighbors()
                        if self.metrics:
                            self.metrics.log_timepoint(self.simulated_time, self._active_count, avg_neighbors_sample)

        except KeyboardInterrupt:
            logging.log_info("Simulation interrupted by user.")
            self.running = False
            
        real_time_end = time.time()
        real_duration = real_time_end - real_time_start
        sim_speedup = self.simulated_time / real_duration if real_duration > 0 else float('inf')
        logging.log_info(f"Simulation complete. {self.simulated_time:.2f}s simulated in {real_duration:.2f}s real time (speedup: {sim_speedup:.2f}x)")
    
    # This method calculates the average number of neighbors for the current buoy array
    def calculate_avg_neighbors(self) -> float: # O(n log n) using k-d tree
        if not self._active_count:
            return 0.0

        # Build the k-d tree using only active buoys' positions
        points = [b.position for b in self.all_buoys if b.active]
        tree = cKDTree(points)

        # Finds all unique pairs of buoys within communication range
        # Each pair (i, j) counts as two directed neighbor relationships
        pairs = tree.query_pairs(self.comm_range_max)
        return (len(pairs) * 2) / self._active_count
    
    # This method calculates the average number of neighbors and records it if metrics collection is enabled
    def calculate_and_record_avg_neighbors(self):
        if not self.metrics:
            return
            
        avg_neighbors: float = self.calculate_avg_neighbors()
        self.metrics.record_avg_neighbors_sample(avg_neighbors)