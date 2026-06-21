import os
import sys

# Suppress traceback on KeyboardInterrupt during imports or anywhere else
def sigint_handler(exctype, value, traceback):
    if issubclass(exctype, KeyboardInterrupt):
        sys.exit(0)
    sys.__excepthook__(exctype, value, traceback)
sys.excepthook = sigint_handler

os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

from buoys.buoy import Buoy
from core.channel import Channel
from config.config_handler import ConfigHandler
from core.simulator import Simulator
from protocols.scheduler import BeaconScheduler
from utils.metrics import Metrics

import argparse
import json
import random
import time

# Argument parser for command-line options to configure the simulation parameters
def parse_args() -> argparse.Namespace:
    cfg = ConfigHandler()
    
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
        default=time.time(),
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
        default=cfg.get('simulation', 'min_buoys'),
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
        "--scenario",
        choices=["static", "ramp", "random"],
        default="static",
        help="Scenario type: static, ramp, or random (default: static)"
    )
    parser.add_argument(
        "--multihop-mode",
        choices=["none", "append", "forwarded"],
        default=cfg.get('simulation', 'multihop_mode'),
        help="Multihop mode to use for the simulation (default: from config)"
    )

    # Parse the command-line arguments and return them as a namespace object
    return parser.parse_args()

# Get random position within the world boundaries
def random_position(world_width: float, world_height: float) -> tuple[float, float]:
    return (
        random.uniform(10, world_width - 10), 
        random.uniform(10, world_height - 10)
    )

# Get random velocity vector for mobile buoys based on a default velocity
def random_velocity(default_velocity: float) -> tuple[float, float]:
    return (
        random.uniform(-1, 1) * default_velocity,
        random.uniform(-1, 1) * default_velocity
    )

# Main function to initialize and run the simulation
def main():
    cfg = ConfigHandler()
    args = parse_args()

    # Unpacking args values
    density: int           = args.density
    duration: float        = args.duration
    fixed_buoy_count: int  = args.fixed_buoy_count
    ideal: bool            = args.ideal
    max_interval: float    = args.max_interval
    min_interval: float    = args.min_interval
    mobile_buoy_count: int = args.mobile_buoy_count
    mode: str              = args.mode
    multihop_mode: str     = args.multihop_mode
    positions_file: str    = args.positions_file
    result_file: str       = args.result_file
    scenario: str          = args.scenario
    seed: float            = args.seed
    static_interval: float = args.static_interval
    world_height: float    = args.world_height
    world_width: float     = args.world_width

    # Inject the multihop mode parsed into the config to be accessed by other components of the simulation
    cfg.set('simulation', 'multihop_mode', multihop_mode)

    # Set the random seed for reproducibility
    random.seed(seed)

    # Load buoy positions from file if provided, otherwise they will be generated randomly
    positions: list[tuple[float, float]] = None
    if positions_file:
        with open(positions_file, "r") as f:
            positions = json.load(f)
    else:
        positions = [
            random_position(world_width, world_height) 
            for _ in range(mobile_buoy_count + fixed_buoy_count)
        ]

    # Initialize the Metrics object if metrics are enabled in the config
    metrics: Metrics | None = None
    if cfg.get('simulation', 'enable_metrics'):
        metrics = Metrics(
            density=density,
            scheduler_type=mode,
            world_width=world_width,
            world_height=world_height,
            mobile_count=mobile_buoy_count,
            fixed_count=fixed_buoy_count,
            duration=duration,
            multihop_mode=multihop_mode  # [none, append, forwarded]
        )

    # Setting up the communication channel for the simulation
    channel = Channel(metrics=metrics, ideal_channel=ideal)

    # Initialization of buoys based on the parameters provided
    buoys: list[Buoy] = []
    default_velocity: float = cfg.get('buoys', 'default_velocity')
    for i in range(mobile_buoy_count + fixed_buoy_count):
        # Determine if this buoy should be mobile or fixed
        mobile = i < mobile_buoy_count
                    
        # Buoy initialization
        buoy = Buoy(
            position=positions[i],
            is_mobile=mobile,
            scheduler=BeaconScheduler(
                scheduler_type=mode,
                static_interval=static_interval,
                min_interval=min_interval,
                max_interval=max_interval,
                default_velocity=default_velocity
            ),
            velocity=random_velocity(default_velocity) if mobile else (0.0, 0.0),
            metrics=metrics is not None
        )

        # Set callbacks for channel interactions
        buoy.channel_is_busy   = channel.is_busy
        buoy.channel_broadcast = channel.broadcast

        # Set callbacks for metrics data collection if metrics are enabled
        if metrics:
            buoy.record_scheduler_latency_callback  = metrics.record_scheduler_latency
            buoy.set_unique_nodes_per_buoy_callback = metrics.set_unique_nodes_per_buoy
            buoy.log_received_callback              = metrics.log_received

        # Add the initialized buoy to the list of buoys
        buoys.append(buoy)

    # Creating the Simulator instance with the initialized buoys, channel, and metrics
    simulator = Simulator(buoys, channel, metrics, scenario, duration)
    simulated_time = simulator.start()

    # If metrics are enabled and there is the output file, then export metrics to a CSV file once simulation is complete
    if metrics and result_file:
        match scenario:
            case "ramp":
                metrics.export_time_series(result_file)
            case "random" | "static":
                summary = metrics.summary(sim_time=simulated_time)
                metrics.export_metrics_to_csv(summary, result_file)

if __name__ == "__main__":
    main()