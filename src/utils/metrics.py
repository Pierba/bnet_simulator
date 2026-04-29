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
        self.beacons_sent: int = 0
        self.beacons_received: int = 0
        self.beacons_lost: int = 0
        self.beacons_collided: int = 0
        self.total_latency: float = 0.0
        self.discovery_times: dict[tuple[UUID, UUID], float] = {}  # {(receiver_id, sender_id): receive_time}
        self.reaction_latencies: list[float] = []
        self.delivered_beacons: set = set()
        self.scheduler_latencies: list[float] = []
        self.potentially_sent: int = 0
        self.actually_received: int = 0
        self.potentially_sent_per_sender: dict[UUID, int] = {}
        self.actually_received_per_sender: dict[UUID, int] = {}
        self.density: float = density
        self.time_series: list = []
        
        self.scheduler_type: str = scheduler_type
        self.world_width: float = world_width
        self.world_height: float = world_height
        self.mobile_buoy_count: int = mobile_count
        self.fixed_buoy_count: int = fixed_count
        self.simulation_duration: float = duration
        self.multihop_mode: str = multihop_mode
        
        # Track unique nodes discovered per buoy
        self.unique_nodes_per_buoy: dict[UUID, set[UUID]] = {}  # {buoy_id: set(node_ids)}
        
        # Track avg_neighbors samples over time
        self.avg_neighbors_samples: list[float] = []
        
        # Track per-hop delivery (hop_count -> count of beacons delivered at that hop)
        self.hop_delivery_counts: dict[int, int] = {}  # {hop_level: count}
        self.hop_sent_counts: dict[int, int] = {}      # {hop_level: count of beacons sent at that hop}

    def log_sent(self):
        self.beacons_sent += 1

    def log_received(self, sender_id: UUID, timestamp: float, receive_time: float, receiver_id: UUID, hop_count: int = 0):
        beacon_key = (sender_id, timestamp)
        
        # Count each reception opportunity on the same basis used by Delivery Ratio.
        self.actually_received += 1
        self.actually_received_per_sender[sender_id] = self.actually_received_per_sender.get(sender_id, 0) + 1

        # Count the reception opportunity for the hop bucket before any deduping.
        self.hop_delivery_counts[hop_count] = self.hop_delivery_counts.get(hop_count, 0) + 1
        if beacon_key in self.delivered_beacons:
            return
        
        self.beacons_received += 1
        self.delivered_beacons.add(beacon_key)
        self.total_latency += receive_time - timestamp
        
        discovery_key = (receiver_id, sender_id)
        if discovery_key in self.discovery_times:
            return

        latency = receive_time - timestamp
        self.reaction_latencies.append(latency)
        self.discovery_times[discovery_key] = receive_time
                

    def log_lost(self, count: int = 1):
        self.beacons_lost += count

    def log_collision(self, count: int = 1):
        self.beacons_collided += count

    def record_scheduler_latency(self, latency: float):
        self.scheduler_latencies.append(latency)

    def avg_scheduler_latency(self) -> float:
        if not self.scheduler_latencies:
            return 0.0
        
        return sum(self.scheduler_latencies) / len(self.scheduler_latencies)

    def log_potentially_sent(self, sender_id: UUID, n_receivers: int, hop_count: int = 0):
        self.potentially_sent += n_receivers
        self.potentially_sent_per_sender[sender_id] = self.potentially_sent_per_sender.get(sender_id, 0) + n_receivers
        # Track per-hop sent
        self.hop_sent_counts[hop_count] = self.hop_sent_counts.get(hop_count, 0) + n_receivers

    def log_actually_received(self, sender_id: UUID):
        # Backward-compatible alias for older call sites.
        self.actually_received += 1
        self.actually_received_per_sender[sender_id] = self.actually_received_per_sender.get(sender_id, 0) + 1

    def log_timepoint(self, sim_time: float, n_buoys: int, avg_neighbors_sample: float = None):
        timepoint = {
            "time": sim_time,
            # True packet delivery ratio (unique beacons delivered / beacons sent)
            "delivery_ratio": self.delivery_ratio(),
            # Reception-based ratio (reception opportunities succeeded / opportunities)
            "reception_ratio": self.reception_ratio(),
            "n_buoys": n_buoys,
            "avg_unique_nodes": self.avg_unique_nodes_discovered()
        }
        
        if avg_neighbors_sample is not None:
            timepoint["avg_neighbors"] = avg_neighbors_sample
            
        self.time_series.append(timepoint)

    def delivery_ratio(self) -> float:
        # True Packet Delivery Ratio: unique beacons received divided by beacons sent
        return self.beacons_received / self.beacons_sent if self.beacons_sent else 0

    def reception_ratio(self) -> float:
        # Reception-opportunity based ratio (existing metric): actually_received / potentially_sent
        return self.actually_received / self.potentially_sent if self.potentially_sent else 0
    
    def avg_unique_nodes_discovered(self) -> float:
        if not self.unique_nodes_per_buoy:
            return 0.0
        
        node_counts = [len(nodes) for nodes in self.unique_nodes_per_buoy.values()]
        return sum(node_counts) / len(node_counts)
    
    def record_avg_neighbors_sample(self, avg_neighbors_value: float):
        self.avg_neighbors_samples.append(avg_neighbors_value)
    
    def get_final_avg_neighbors(self) -> float:
        if not self.avg_neighbors_samples:
            return 0.0
        return sum(self.avg_neighbors_samples) / len(self.avg_neighbors_samples)
    
    def per_hop_delivery_ratios(self) -> dict[int, float]:
        """Return delivery ratio per hop level."""
        ratios = {}
        for hop in self.hop_sent_counts.keys():
            sent = self.hop_sent_counts.get(hop, 0)
            delivered = self.hop_delivery_counts.get(hop, 0)
            ratios[hop] = delivered / sent if sent > 0 else 0.0
        return ratios
    
    def summary(self, sim_time: float):
        avg_latency = self.total_latency / self.beacons_received if self.beacons_received else 0
        avg_unique_nodes = self.avg_unique_nodes_discovered()
        final_avg_neighbors = self.get_final_avg_neighbors()
        
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
            "Avg Latency": avg_latency,
            "Avg Scheduler Latency": self.avg_scheduler_latency(),
            "Delivery Ratio": self.delivery_ratio(),
            "Reception Ratio": self.reception_ratio(),
            "Collision Rate": self.beacons_collided / self.potentially_sent if self.potentially_sent else 0,
            "Avg Reaction Latency": (
                sum(self.reaction_latencies) / len(self.reaction_latencies)
                if self.reaction_latencies else 0
            ),
            "Throughput (beacons/sec)": (
                self.actually_received / sim_time
                if sim_time > 0 else 0
            ),
            "Potentially Sent": self.potentially_sent,
            "Actually Received": self.actually_received,
            "Average Neighbors": final_avg_neighbors,
            "Avg Unique Nodes Discovered": avg_unique_nodes,
        }

        if self.density is not None:
            summary["Density"] = self.density
        
        # Add per-hop delivery ratios
        for hop, ratio in self.per_hop_delivery_ratios().items():
            summary[f"DR_Hop{hop}"] = ratio
            
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