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
    def __init__(self, buoys: List[Buoy], channel: Channel, metrics: Metrics, ramp: bool = False, duration: float = None):
        cfg = ConfigHandler()
        
        self.ramp: bool = ramp
        self.all_buoys: List[Buoy] = buoys
        # In ramp mode, we start with only 2 buoys and add the rest gradually. In non-ramp mode, we start with all buoys active.
        self.buoys: List[Buoy] = self.all_buoys.copy()[:2] if ramp else buoys
        self.channel: Channel = channel
        self.metrics: Metrics = metrics
        
        self.first_change: bool = True
        self.next_buoy_change: float = 0
        self.duration: float = duration if duration is not None else cfg.get('simulation', 'duration')
        
        self.neighbor_timeout: float = cfg.get('scheduler', 'neighbor_timeout')
        self.comm_range_max: float = cfg.get('network', 'communication_range_max')

        self.channel.set_buoys(self.buoys)
        self.channel.schedule_callback = self.schedule_event
        self.running: bool = False
        self.simulated_time: float = 0.0
        
        # Making all buoys able to access the schedule_event method of the simulator for scheduling their own events
        for buoy in self.all_buoys:
            buoy.schedule_callback = self.schedule_event

        # Event variables for managing the event queue
        self.event_queue: list = []
        self.event_counter: int = 0

    # Adding event to event_queue with a small epsilon to ensure correct ordering of events scheduled at the same time
    def schedule_event(self, time: float, event_type: EventType, target_obj: Buoy | Channel, data: Optional[Dict] = None):
        event = Event(time, event_type, target_obj, data)
        epsilon: float = self.event_counter * 1e-10
        self.event_counter += 1
        heapq.heappush(self.event_queue, (event.time + epsilon, self.event_counter, event))
    
    def _get_next_event(self) -> Optional[Event]:
        if not self.event_queue:
            return None
        _, _, event = heapq.heappop(self.event_queue)
        return event

    def _schedule_initial_events(self):
        # Schedule initial events for all buoys
        for buoy in self.buoys:
            initial_offset = random.uniform(0, 1.0)
            self.schedule_event(initial_offset, EventType.SCHEDULER_CHECK, buoy)
            self.schedule_event(self.neighbor_timeout, EventType.NEIGHBOR_CLEANUP, buoy)
            
            if buoy.is_mobile:
                self.schedule_event(0.1, EventType.BUOY_MOVEMENT, buoy)
        
        # Schedule initial channel update event
        self.schedule_event(1.0, EventType.CHANNEL_UPDATE, self.channel)
        self.schedule_event(30.0, EventType.BUOY_ARRAY_UPDATE, self)
        
        # Schedule periodic avg_neighbors calculation every 30 seconds
        self.schedule_event(30.0, EventType.AVG_NEIGHBORS_CALCULATION, self)

    def update_buoy_array(self, sim_time: float):
        if self.ramp:
            self._update_buoy_array_ramp(sim_time)
        else:
            self._update_buoy_array_random(sim_time)
        
        # Recalculate avg_neighbors after buoy array changes
        self.calculate_and_record_avg_neighbors()

    # This method randomly adds or removes buoys from the active buoy array while ensuring that we don't go below a 
    # minimum number of buoys or above the total number of buoys
    # It also ensures that the first change is a significant removal
    def _update_buoy_array_random(self, sim_time: float):
        
        # Determine active and inactive buoys
        active_buoys = self.buoys.copy()
        inactive_buoys = [b for b in self.all_buoys if b not in active_buoys]
        total_buoys = len(self.all_buoys)

        # Ensure we don't remove too many buoys and maintain a minimum number of active buoys
        if self.first_change or (random.random() >= 0.5 and len(active_buoys) > max(3, int(total_buoys * 0.2))):
            min_buoys = max(3, int(total_buoys * 0.2))

            if len(active_buoys) > min_buoys:
                remove_percentage = 0.5 if self.first_change else 0.4
                max_to_remove = min(len(active_buoys) - min_buoys, 
                                  max(2, int(total_buoys * remove_percentage)))
                
                num_to_remove = random.randint(1, max_to_remove)
                buoys_to_remove = random.sample(active_buoys, num_to_remove)

                # Remove the selected buoys from the active list and log the removals                
                for buoy in buoys_to_remove:
                    self.buoys.remove(buoy)
                    logging.log_info(f"Removed buoy {str(buoy.id)[:6]} at {sim_time:.2f}s")

                # Update the channel with the new buoy array and log the change
                self.channel.set_buoys(self.buoys)
                logging.log_info(f"Removed {num_to_remove} buoys, now {len(self.buoys)} active at {sim_time:.2f}s")
        
        # If we didn't remove buoys, we have a chance to add some back in, but we won't add too many
        elif inactive_buoys:
            max_to_add = min(len(inactive_buoys), max(2, int(total_buoys * 0.4)))
            
            num_to_add = random.randint(1, max_to_add)
            buoys_to_add = random.sample(inactive_buoys, num_to_add)
            
            for buoy in buoys_to_add:
                self.buoys.append(buoy)
                initial_offset = random.uniform(0, 1.0) 
                self.schedule_event(sim_time + initial_offset, EventType.SCHEDULER_CHECK, buoy)
                self.schedule_event(sim_time + self.neighbor_timeout, EventType.NEIGHBOR_CLEANUP, buoy)
                if buoy.is_mobile:
                    self.schedule_event(sim_time + 0.1, EventType.BUOY_MOVEMENT, buoy)
                
                logging.log_info(f"Added buoy {str(buoy.id)[:6]} at {sim_time:.2f}s")
                
            self.channel.set_buoys(self.buoys)
            logging.log_info(f"Added {num_to_add} buoys, now {len(self.buoys)} active at {sim_time:.2f}s")

        if self.first_change:
            # Turning off the first change flag after the first buoy change to ensure the next changes are more balanced
            logging.log_info("First buoy change: forced major removal operation finished")
            self.first_change = False

        next_change_time = sim_time + random.uniform(15, 20)
        self.schedule_event(next_change_time, EventType.BUOY_ARRAY_UPDATE, self)

    def _update_buoy_array_ramp(self, sim_time: float):
        active_buoys = self.buoys.copy()
        inactive_buoys = [b for b in self.all_buoys if b not in active_buoys]
        current_count = len(active_buoys)
        total_buoys = len(self.all_buoys)
        buoys_to_add = total_buoys - 2
        add_interval = (self.duration / buoys_to_add) if buoys_to_add > 0 else self.duration
    
        if current_count < total_buoys:
            if inactive_buoys:
                buoy = inactive_buoys[0]
                self.buoys.append(buoy)
                initial_offset = random.uniform(0, 1.0)
                self.schedule_event(sim_time + initial_offset, EventType.SCHEDULER_CHECK, buoy)
                self.schedule_event(sim_time + self.neighbor_timeout, EventType.NEIGHBOR_CLEANUP, buoy)
            self.channel.set_buoys(self.buoys)
            self.schedule_event(sim_time + add_interval, EventType.BUOY_ARRAY_UPDATE, self)

    def handle_event(self, event: Event, sim_time: float):
        match event.event_type:
            case EventType.BUOY_ARRAY_UPDATE:
                self.update_buoy_array(sim_time)
            case EventType.AVG_NEIGHBORS_CALCULATION:
                self.calculate_and_record_avg_neighbors()
            case _:
                logging.log_error(f"Simulator received unhandled event: {event.event_type}")
            
        # Schedule next calculation
        self.schedule_event(sim_time + 30.0, EventType.AVG_NEIGHBORS_CALCULATION, self)

    def start(self):
        self.running = True
        real_time_start = time.time()
        logging.reset() # Resetting metrics and logs at the start of the simulation

        # Calculate initial avg_neighbors and schedule initial events
        self.calculate_and_record_avg_neighbors()
        self._schedule_initial_events()
        
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
                    logging.log_info(f"Processing {event}")
                    
                if self.simulated_time > 0 and int(self.simulated_time) % 10 == 0:
                    logging.log_info(f"Time: {self.simulated_time:.2f}s, Event queue size: {len(self.event_queue)}")
                
                # Handle the event and catch any exceptions to prevent the simulation from crashing
                try:
                    event.target_obj.handle_event(event, self.simulated_time)
                except Exception as e:
                    logging.log_error(f"Error handling event {event}: {str(e)}")
                
                if self.ramp and self.simulated_time > 0 and int(self.simulated_time) % 5 == 0:
                    avg_neighbors_sample = self.calculate_avg_neighbors()
                    self.metrics.log_timepoint(self.simulated_time, len(self.buoys), avg_neighbors_sample)

        except KeyboardInterrupt:
            logging.log_info("Simulation interrupted by user.")
            self.running = False
            
        real_time_end = time.time()
        real_duration = real_time_end - real_time_start
        sim_speedup = self.simulated_time / real_duration if real_duration > 0 else float('inf')
        logging.log_info(f"Simulation complete. {self.simulated_time:.2f}s simulated in {real_duration:.2f}s real time (speedup: {sim_speedup:.2f}x)")
    
    # This method calculates the average number of neighbors for the current buoy array
    def calculate_avg_neighbors(self) -> float: # O(n log n) using k-d tree
        if not self.buoys:
            return 0.0
            
        # Build the k-d tree using the buoys' positions
        points = [b.position for b in self.buoys]
        tree = cKDTree(points)
        
        # Finds all unique pairs of buoys within communication range
        # This returns a set of (i, j) pairs
        pairs = tree.query_pairs(self.comm_range_max)
        
        # Each unique pair (i, j) means: i-buoy is a neighbor of j-buoy, and j-buoy is a neighbor of i-buoy
        total_neighbors = len(pairs) * 2
        
        return total_neighbors / len(self.buoys)
    
    # This method calculates the average number of neighbors and records it if metrics collection is enabled
    def calculate_and_record_avg_neighbors(self) -> Optional[float]:
        if not self.metrics:
            return None
        
        avg_neighbors: float = self.calculate_avg_neighbors()
        self.metrics.record_avg_neighbors_sample(avg_neighbors)
        return avg_neighbors 