import os
import csv
from utils import logging

# Class to track and summarize metrics for the BNet simulation
class Metrics:
    def __init__(self, density=None):
        self.beacons_sent: int = 0
        self.beacons_received: int = 0
        self.beacons_lost: int = 0
        self.beacons_collided: int = 0
        self.total_latency: float = 0.0
        self.discovery_times: dict = {}
        self.reaction_latencies: list[float] = []
        self.delivered_beacons: set = set()
        self.scheduler_latencies: list[float] = []
        self.potentially_sent: int = 0
        self.actually_received: int = 0
        self.potentially_sent_per_sender: dict = {}
        self.actually_received_per_sender: dict = {}
        self.avg_neighbors: float = 0.0
        self.density: float = density
        self.time_series: list = []
        
        self.scheduler_type: str = None
        self.world_width: float = None
        self.world_height: float = None
        self.mobile_buoy_count: int = None
        self.fixed_buoy_count: int = None
        self.simulation_duration: float = None
        self.multihop_mode: str = None
        
        # Track unique nodes discovered per buoy
        self.unique_nodes_per_buoy: dict = {}  # {buoy_id: set(node_ids)}
        
        # Track avg_neighbors samples over time
        self.avg_neighbors_samples: list[float] = []

    # Method to set simulation info for context in metrics
    def set_simulation_info(self, scheduler_type, world_width, world_height, mobile_count, fixed_count, duration, multihop_mode=None):
        self.scheduler_type = scheduler_type
        self.world_width = world_width
        self.world_height = world_height
        self.mobile_buoy_count = mobile_count
        self.fixed_buoy_count = fixed_count
        self.simulation_duration = duration
        self.multihop_mode = multihop_mode

    def log_sent(self):
        self.beacons_sent += 1

    def log_received(self, sender_id, timestamp, receive_time, receiver_id=None):
        key = (sender_id, timestamp)
        if key not in self.delivered_beacons:
            self.beacons_received += 1
            self.delivered_beacons.add(key)
            self.total_latency += receive_time - timestamp

            if receiver_id is not None:
                if receiver_id not in self.discovery_times:
                    self.discovery_times[receiver_id] = {}
                if sender_id not in self.discovery_times[receiver_id]:
                    latency = receive_time - timestamp
                    self.reaction_latencies.append(latency)
                    self.discovery_times[receiver_id][sender_id] = receive_time
                
                # Track unique node discovery (direct sender)
                if receiver_id not in self.unique_nodes_per_buoy:
                    self.unique_nodes_per_buoy[receiver_id] = set()
                self.unique_nodes_per_buoy[receiver_id].add(sender_id)
    
    def log_nodes_discovered_from_neighbors(self, receiver_id, neighbor_ids):
        if receiver_id not in self.unique_nodes_per_buoy:
            self.unique_nodes_per_buoy[receiver_id] = set()
        
        for node_id in neighbor_ids:
            if node_id != receiver_id:  # Don't add self
                self.unique_nodes_per_buoy[receiver_id].add(node_id)

    def log_lost(self, count: int = 1):
        self.beacons_lost += count

    def log_collision(self, count: int = 1):
        self.beacons_collided += count

    def record_scheduler_latency(self, latency: float):
        self.scheduler_latencies.append(latency)

    def avg_scheduler_latency(self) -> float:
        return sum(self.scheduler_latencies) / len(self.scheduler_latencies) if self.scheduler_latencies else 0.0

    def log_potentially_sent(self, sender_id, n_receivers):
        self.potentially_sent += n_receivers
        self.potentially_sent_per_sender[sender_id] = self.potentially_sent_per_sender.get(sender_id, 0) + n_receivers

    def log_actually_received(self, sender_id):
        self.actually_received += 1
        self.actually_received_per_sender[sender_id] = self.actually_received_per_sender.get(sender_id, 0) + 1

    def log_timepoint(self, sim_time, n_buoys, avg_neighbors_sample=None):
        timepoint = {
            "time": sim_time,
            "delivery_ratio": self.delivery_ratio(),
            "n_buoys": n_buoys,
            "avg_unique_nodes": self.avg_unique_nodes_discovered()
        }
        
        if avg_neighbors_sample is not None:
            timepoint["avg_neighbors"] = avg_neighbors_sample
            
        self.time_series.append(timepoint)

    def delivery_ratio(self):
        return self.actually_received / self.potentially_sent if self.potentially_sent else 0
    
    def avg_unique_nodes_discovered(self):
        if not self.unique_nodes_per_buoy:
            return 0.0
        
        node_counts = [len(nodes) for nodes in self.unique_nodes_per_buoy.values()]
        return sum(node_counts) / len(node_counts) if node_counts else 0.0
    
    def record_avg_neighbors_sample(self, avg_neighbors_value):
        self.avg_neighbors_samples.append(avg_neighbors_value)
    
    def get_final_avg_neighbors(self):
        if not self.avg_neighbors_samples:
            return self.avg_neighbors
        return sum(self.avg_neighbors_samples) / len(self.avg_neighbors_samples)
    
    def summary(self, sim_time: float):
        avg_latency = self.total_latency / self.beacons_received if self.beacons_received else 0
        avg_unique_nodes = self.avg_unique_nodes_discovered()  # UPDATED: Use new method
        final_avg_neighbors = self.get_final_avg_neighbors()
        
        base_summary = {
            "Scheduler Type": self.scheduler_type or "unknown",
            "Multihop Mode": self.multihop_mode or "none",
            "World Size": f"{self.world_width}x{self.world_height}" if self.world_width else "unknown",
            "Mobile Buoys": self.mobile_buoy_count or 0,
            "Fixed Buoys": self.fixed_buoy_count or 0,
            "Simulation Duration": self.simulation_duration or sim_time,
            "Sent": self.beacons_sent,
            "Received": self.beacons_received,
            "Lost": self.beacons_lost,
            "Collisions": self.beacons_collided,
            "Avg Latency": avg_latency,
            "Avg Scheduler Latency": self.avg_scheduler_latency(),
            "Delivery Ratio": self.delivery_ratio(),
            "Collision Rate": self.beacons_collided / self.potentially_sent if self.potentially_sent else 0,
            "Avg Reaction Latency": (
                sum(self.reaction_latencies) / len(self.reaction_latencies)
                if self.reaction_latencies else 0
            ),
            "Throughput (beacons/sec)": (
                self.beacons_received / sim_time
                if sim_time > 0 else 0
            ),
            "Potentially Sent": self.potentially_sent,
            "Actually Received": self.actually_received,
            "Average Neighbors": final_avg_neighbors,
            "Avg Unique Nodes Discovered": avg_unique_nodes,  # UPDATED: New name
        }

        summary = {**base_summary}
        if self.density is not None:
            summary["Density"] = self.density
        return summary

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

    def set_avg_neighbors(self, avg_neighbors):
        self.avg_neighbors = avg_neighbors