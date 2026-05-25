from typing import Any, Optional
import os
import csv
from utils import logging
from uuid import UUID

# Class to track and summarize metrics for the BNet simulation
class Metrics:
    def __init__(
        self,
        density=None,
        scheduler_type=None,
        world_width=None,
        world_height=None,
        mobile_count=None,
        fixed_count=None,
        duration=None,
        multihop_mode=None,
    ):
        # Store configuration parameters for context in the summary
        self.density: float             = density
        self.scheduler_type: str        = scheduler_type
        self.world_width: float         = world_width
        self.world_height: float        = world_height
        self.mobile_buoy_count: int     = mobile_count
        self.fixed_buoy_count: int      = fixed_count
        self.simulation_duration: float = duration
        self.multihop_mode: str         = multihop_mode
        
        # Metrics tracking
        self.actually_received: int                         = 0
        self.avg_neighbors_count: int                       = 0
        self.avg_neighbors_sum: float                       = 0.0
        self.beacons_sent: int                              = 0
        self.beacons_received: int                          = 0
        self.beacons_lost: int                              = 0
        self.beacons_collided: int                          = 0
        self.delivered_beacons: dict[UUID, float]           = {}
        self.discovered_pairs: dict[UUID, set[UUID]]        = {}
        self.potentially_sent: int                          = 0
        self.reaction_latency_count: int                    = 0
        self.reaction_latency_sum: float                    = 0.0
        self.scheduler_latency_count: int                   = 0
        self.scheduler_latency_sum: float                   = 0.0
        self.time_series: list                              = []
        self.total_latency: float                           = 0.0
        self.total_successful_receivers: int                = 0
        self.unique_nodes_per_buoy: dict[UUID, set[UUID]]   = {} 
        
    # Set of unique nodes discovered by each buoy
    def set_unique_nodes_per_buoy(self, buoy_id: UUID, unique_nodes: set[UUID]):
        if buoy_id not in self.unique_nodes_per_buoy:
            self.unique_nodes_per_buoy[buoy_id] = set()
        self.unique_nodes_per_buoy[buoy_id].update(unique_nodes)

    # Log a sent beacon
    def log_sent(self):
        self.beacons_sent += 1

    # Log a received beacon and tracks unique deliveries and latency
    def log_received(self, sender_id: UUID, timestamp: float, receive_time: float, receiver_id: UUID):
        # Count each reception opportunity on the same basis used by Delivery Ratio.
        self.actually_received += 1

        # If this beacon has already been counted as delivered, skip it
        last_ts = self.delivered_beacons.get(sender_id)
        if last_ts is not None and last_ts >= timestamp:
            return

        # Update the number of unique beacons received and total latency
        self.delivered_beacons[sender_id] = timestamp
        self.beacons_received += 1
        self.total_latency += receive_time - timestamp

        # Only count the reaction latency for the first time this receiver discovers this sender
        seen_senders = self.discovered_pairs.get(receiver_id)
        if seen_senders is None:
            seen_senders = set()
            self.discovered_pairs[receiver_id] = seen_senders
        elif sender_id in seen_senders:
            return

        seen_senders.add(sender_id)
        self.reaction_latency_count += 1
        self.reaction_latency_sum += receive_time - timestamp
                

    # Log a lost beacon
    def log_lost(self, count: int = 1):
        self.beacons_lost += count

    # Log a collision
    def log_collision(self, count: int = 1):
        self.beacons_collided += count

    # Log scheduler latency for a beacon
    def record_scheduler_latency(self, latency: float):
        self.scheduler_latency_sum += latency
        self.scheduler_latency_count += 1

    # Average scheduler latency across all recorded samples
    def avg_scheduler_latency(self) -> float:
        if not self.scheduler_latency_count:
            return 0.0

        return self.scheduler_latency_sum / self.scheduler_latency_count

    # Log the number of potential receivers for a beacon
    def log_potentially_sent(self, n_receivers: int):
        self.potentially_sent += n_receivers

    # Log the number of successful receivers for a beacon
    def log_successful_receivers(self, count: int):
        self.total_successful_receivers += count

    # Calculate Packet Delivery Ratio: successful receivers / potential receivers
    def packet_delivery_ratio(self) -> float:
        return self.total_successful_receivers / self.potentially_sent if self.potentially_sent else 0.0

    # Log a timepoint for time-series analysis, including delivery ratio and PDR at this moment
    def log_timepoint(self, sim_time: float, n_buoys: int, avg_neighbors_sample: Optional[float] = None):
        timepoint = {
            "time": sim_time,
            # True packet delivery ratio (unique beacons delivered / beacons sent)
            "delivery_ratio": self.delivery_ratio(),
            # PDR: successful receivers / potential receivers
            "pdr": self.packet_delivery_ratio(),
            "n_buoys": n_buoys,
            "avg_unique_nodes": self.avg_unique_nodes_discovered()
        }
        
        if avg_neighbors_sample is not None:
            timepoint["avg_neighbors"] = avg_neighbors_sample
            
        self.time_series.append(timepoint)

    # Calculate True Packet Delivery Ratio: unique beacons delivered / beacons sent
    def delivery_ratio(self) -> float:
        return self.beacons_received / self.beacons_sent if self.beacons_sent else 0.0

    # Calculate the average number of unique nodes discovered per buoy
    def avg_unique_nodes_discovered(self) -> float:
        if not self.unique_nodes_per_buoy:
            return 0.0
        
        node_counts = [len(nodes) for nodes in self.unique_nodes_per_buoy.values()]
        return sum(node_counts) / len(node_counts)
    
    # Record a sample of the average number of neighbors for time-series analysis
    def record_avg_neighbors_sample(self, avg_neighbors_value: float):
        self.avg_neighbors_sum += avg_neighbors_value
        self.avg_neighbors_count += 1

    # Calculate the final average number of neighbors from the recorded samples
    def get_final_avg_neighbors(self) -> float:
        if not self.avg_neighbors_count:
            return 0.0
        return self.avg_neighbors_sum / self.avg_neighbors_count
    
    # Generate a summary of all metrics for the simulation run
    def summary(self, sim_time: float) -> dict[str]:
        summary = {
            "Scheduler Type": self.scheduler_type or "unknown",
            "Multihop Mode": self.multihop_mode or "none",
            "World Size": f"{self.world_width}x{self.world_height}" if self.world_width else "unknown",
            "Mobile Buoys": self.mobile_buoy_count or 0,
            "Fixed Buoys": self.fixed_buoy_count or 0,
            "Simulation Duration": self.simulation_duration or sim_time,
            "Sent": self.beacons_sent,
            "Unique Beacons Received": self.beacons_received,
            "Lost": self.beacons_lost,
            "Collisions": self.beacons_collided,
            "Avg Latency": self.total_latency / self.beacons_received if self.beacons_received else 0,
            "Avg Scheduler Latency": self.avg_scheduler_latency(),
            "Delivery Ratio": self.delivery_ratio(),
            "PDR": self.packet_delivery_ratio(),
            "Collision Rate": self.beacons_collided / self.potentially_sent if self.potentially_sent else 0,
            "Avg Reaction Latency": (
                self.reaction_latency_sum / self.reaction_latency_count
                if self.reaction_latency_count else 0
            ),
            "Throughput (beacons/sec)": (
                self.actually_received / sim_time
                if sim_time > 0 else 0
            ),
            "Potentially Sent": self.potentially_sent,
            "Actually Received": self.actually_received,
            "Successful Receivers": self.total_successful_receivers,
            "Average Neighbors": self.get_final_avg_neighbors(),
            "Avg Unique Nodes Discovered": self.avg_unique_nodes_discovered(),
        }

        if self.density is not None:
            summary["Density"] = self.density
            
        return summary

    # Export the summary metrics to a CSV file for later plotting
    def export_metrics_to_csv(self, summary, filename=None):
        if filename is None:
            results_dir = os.path.join("metrics", "test_results")
            os.makedirs(results_dir, exist_ok=True)
            filename = (
                f"{self.scheduler_type or 'unknown'}_"
                f"{int(self.world_width or 0)}x{int(self.world_height or 0)}_"
                f"mob{self.mobile_buoy_count or 0}_fix{self.fixed_buoy_count or 0}.csv"
            )
            filepath = os.path.join(results_dir, filename)
        else:
            filepath = filename
            os.makedirs(os.path.dirname(filepath), exist_ok=True)

        with open(filepath, mode="w", newline="") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["Metric", "Value"])
            for key, value in summary.items():
                writer.writerow([key, value])
        logging.log_info(f"Metrics exported to {filepath}")

    # Export the time-series data to a CSV file for later plotting
    def export_time_series(self, filename=None):
        import pandas as pd
        if filename is None:
            results_dir = os.path.join("metrics", "test_results")
            os.makedirs(results_dir, exist_ok=True)
            filename = (
                f"{self.scheduler_type or 'unknown'}_"
                f"{int(self.world_width or 0)}x{int(self.world_height or 0)}_"
                f"mob{self.mobile_buoy_count or 0}_fix{self.fixed_buoy_count or 0}_timeseries.csv"
            )
            filepath = os.path.join(results_dir, filename)
        else:
            filepath = filename
            os.makedirs(os.path.dirname(filepath), exist_ok=True)

        df = pd.DataFrame(self.time_series)
        df.to_csv(filepath, index=False)
        logging.log_info(f"Time series exported to {filepath}")