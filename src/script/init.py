import os
from typing import List, Tuple
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

from core.simulator import Simulator
from core.channel import Channel
from buoys.buoy import Buoy
from config.config_handler import ConfigHandler
from utils.metrics import Metrics
import random
import time
import argparse
import json

def parse_args():
    cfg = ConfigHandler()
    
    # Argument parser for command-line options to configure the simulation parameters
    parser = argparse.ArgumentParser(description="Run the BNet Simulator")
    parser.add_argument(
        "--mode",
        choices=["static", "dynamic_adab", "dynamic_acab"],
        default="static",
        help="Scheduler mode to use for the simulation (default: static)"
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=cfg.get('simulation', 'duration'),
        help="Duration of the simulation in seconds"
    )
    parser.add_argument(
        "--seed",
        type=float,
        default=None,
        help="Random seed for reproducibility"
    )
    parser.add_argument(
        "--world-width",
        type=float,
        default=cfg.get('world', 'width'),
        help="Width of the simulation world"
    )
    parser.add_argument(
        "--world-height",
        type=float,
        default=cfg.get('world', 'height'),
        help="Height of the simulation world"
    )
    parser.add_argument(
        "--mobile-buoy-count",
        type=int,
        default=10,
        help="Number of mobile buoys"
    )
    parser.add_argument(
        "--fixed-buoy-count",
        type=int,
        default=10,
        help="Number of fixed buoys"
    )
    parser.add_argument(
        "--result-file",
        type=str,
        default=None,
        help="Filename for metrics CSV output"
    )
    parser.add_argument(
        "--positions-file",
        type=str,
        default=None,
        help="Path to a file with buoy positions"
    )
    parser.add_argument(
        "--density",
        type=int,
        default=None,
        help="Density value for this scenario"
    )
    parser.add_argument(
        "--ideal",
        action='store_true',
        help="Use ideal channel conditions"
    )
    parser.add_argument(
        "--static-interval",
        type=float,
        default=cfg.get('scheduler', 'static_interval'),
        help="Interval for static scheduler in seconds"
    )
    parser.add_argument(
        "--min-interval",
        type=float,
        default=cfg.get('scheduler', 'beacon_min_interval'),
        help="Minimum interval for dynamic schedulers in seconds"
    )
    parser.add_argument(
        "--max-interval",
        type=float,
        default=cfg.get('scheduler', 'beacon_max_interval'),
        help="Maximum interval for dynamic schedulers in seconds"
    )
    parser.add_argument(
        "--ramp",
        action='store_true',
        help="Use ramp scenario"
    )

    # Parse the command-line arguments and return them as a namespace object 
    return parser.parse_args()

# Get random position within the world boundaries
def random_position(world_width, world_height) -> Tuple[float, float]:
    x = random.uniform(10, world_width - 10)
    y = random.uniform(10, world_height - 10)
    return (x, y)

def random_velocity(default_velocity) -> Tuple[float, float]:
    return (
        random.uniform(-1, 1) * default_velocity,
        random.uniform(-1, 1) * default_velocity
    )

def main():
    cfg = ConfigHandler()
    args = parse_args()

    # Unpacking args values
    mode: str = args.mode
    duration: float = args.duration
    seed: float = args.seed
    world_width: float = args.world_width
    world_height: float = args.world_height
    mobile_buoy_count: int = args.mobile_buoy_count
    fixed_buoy_count: int = args.fixed_buoy_count
    result_file: str = args.result_file
    positions_file: str = args.positions_file
    density: int = args.density
    ideal: bool = args.ideal
    static_interval: float = args.static_interval
    min_interval: float = args.min_interval
    max_interval: float = args.max_interval
    ramp: bool = args.ramp

    # Set the random seed if provided, otherwise use the current time    
    if seed is not None:
        random.seed(seed)
    else:
        random.seed(time.time())

    # Load buoy positions from file if provided, otherwise they will be generated randomly
    positions: List[Tuple[float, float]] = None
    if positions_file:
        with open(positions_file, "r") as f:
            positions = json.load(f)
    else:
        positions = [random_position(world_width, world_height) for _ in range(mobile_buoy_count + fixed_buoy_count)]

    # Initialize the Metrics object if metrics collection is enabled in the configuration
    # This object will track various performance metrics throughout the simulation
    metrics = None
    if cfg.get('simulation', 'enable_metrics'):
        metrics = Metrics(density=density)
        multihop_mode = cfg.get('simulation', 'multihop_mode')  # [none, append, forwarded]
        
        metrics.set_simulation_info(
            scheduler_type=mode,
            world_width=world_width,
            world_height=world_height,
            mobile_count=mobile_buoy_count,
            fixed_count=fixed_buoy_count,
            duration=duration,
            multihop_mode=multihop_mode
        )

    # Settin up the communication channel for the simulation
    channel = Channel(metrics=metrics, ideal_channel=ideal)

    # Initialization of buoys based on the parameters provided
    buoys = []
    default_battery = cfg.get('buoys', 'default_battery')
    default_velocity = cfg.get('buoys', 'default_velocity')
    for i in range(mobile_buoy_count + fixed_buoy_count):
        # Determine if this buoy should be mobile or fixed
        mobile = i < mobile_buoy_count
                    
        # Buoy initialization
        buoy = Buoy(
            channel=channel,
            position=positions[i],
            is_mobile=mobile,
            battery=default_battery,
            velocity=random_velocity(default_velocity) if mobile else (0.0, 0.0),
            metrics=metrics
        )

        # Set the scheduler type: ['static', 'dynamic_adab', 'dynamic_acab']
        buoy.scheduler.scheduler_type = mode

        # Set scheduler intervals based on command-line arguments or configuration values
        buoy.scheduler.static_interval = static_interval
        buoy.scheduler.min_interval = min_interval
        buoy.scheduler.max_interval = max_interval

        buoys.append(buoy)
    
    simulator = Simulator(buoys, channel, metrics, ramp, duration)
    simulator.start()

    if metrics:
        if not ramp:
            summary = metrics.summary(simulator.simulated_time)
            metrics.export_metrics_to_csv(summary, filename=result_file)
        else:
            metrics.export_time_series(result_file)

if __name__ == "__main__":
    main()