import os
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
        type=float,
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
        "--ramp",
        action='store_true',
        help="Use ramp scenario"
    )

    # Parse the command-line arguments and return them as a namespace object 
    return parser.parse_args()

# Get random position within the world boundaries
def random_position(world_width, world_height):
    x = random.uniform(10, world_width - 10)
    y = random.uniform(10, world_height - 10)
    return (x, y)

def random_velocity(default_velocity):
    return (
        random.uniform(-1, 1) * default_velocity,
        random.uniform(-1, 1) * default_velocity
    )

def main():
    cfg = ConfigHandler()
    args = parse_args()

    # Set the random seed if provided, otherwise use the current time    
    if args.seed is not None:
        random.seed(args.seed)
    else:
        random.seed(time.time())

    # Load buoy positions from file if provided, otherwise they will be generated randomly in the simulation
    positions = None
    if args.positions_file:
        with open(args.positions_file, "r") as f:
            positions = json.load(f)

    # Initialize the Metrics object if metrics collection is enabled in the configuration
    # This object will track various performance metrics throughout the simulation
    metrics = None
    if cfg.get('simulation', 'enable_metrics'):
        metrics = Metrics(density=args.density)
        multihop_mode = cfg.get('simulation', 'multihop_mode')  # [none, append, forwarded]
        
        metrics.set_simulation_info(
            scheduler_type=args.mode,
            world_width=args.world_width,
            world_height=args.world_height,
            mobile_count=args.mobile_buoy_count,
            fixed_count=args.fixed_buoy_count,
            duration=args.duration,
            multihop_mode=multihop_mode
        )

    # Settin up the communication channel for the simulation
    channel = Channel(metrics=metrics, ideal_channel=args.ideal)
    default_battery = cfg.get('buoys', 'default_battery')
    default_velocity = cfg.get('buoys', 'default_velocity')
    # mobile_buoys = []
    # for i in range(args.mobile_buoy_count):
    #     pos = positions[i] if positions else random_position(args.world_width, args.world_height)
    #     buoy = Buoy(
    #         channel=channel,
    #         position=pos,
    #         is_mobile=True,
    #         battery=default_battery,
    #         velocity=random_velocity(default_velocity),
    #         metrics=metrics
    #     )
    #     buoy.scheduler.scheduler_type = args.mode
    #     buoy.scheduler.static_interval = args.static_interval
    #     buoy.scheduler.min_interval = args.static_interval
    #     mobile_buoys.append(buoy)

    # static_buoys = []
    # for i in range(args.fixed_buoy_count):
    #     pos = positions[i] if positions else random_position(args.world_width, args.world_height)
    #     buoy = Buoy(
    #         channel=channel,
    #         position=pos,
    #         is_mobile=False,
    #         battery=default_battery,
    #         metrics=metrics
    #     )
    #     buoy.scheduler.scheduler_type = args.mode
    #     buoy.scheduler.static_interval = args.static_interval
    #     buoy.scheduler.min_interval = args.static_interval
    #     static_buoys.append(buoy)

    buoys = []
    for i in range(args.mobile_buoy_count + args.fixed_buoy_count):
        # Determine if this buoy should be mobile or fixed
        mobile = i < args.mobile_buoy_count
        # Get the position for this buoy from the positions file if provided, otherwise generate a random position
        pos = positions[i] if positions else random_position(args.world_width, args.world_height)
        
        # Buoy initialization
        buoy = Buoy(
            channel=channel,
            position=pos,
            is_mobile=mobile,
            battery=default_battery,
            velocity=random_velocity(default_velocity) if mobile else default_velocity,
            metrics=metrics
        )

        # Set the scheduler type: ['static', 'dynamic_adab', 'dynamic_acab']
        buoy.scheduler.scheduler_type = args.mode

        # ??? Why set both static and min interval to the same value?
        buoy.scheduler.static_interval = args.static_interval
        buoy.scheduler.min_interval = args.static_interval
        
        buoys.append(buoy)

    # Setting the buoys in the channel so that it can manage their transmissions and interactions
    channel.set_buoys(buoys)

    
    simulator = Simulator(buoys, channel, metrics, args.ramp, args.duration)
    simulator.start()

    if metrics:
        if not args.ramp:
            summary = metrics.summary(simulator.simulated_time)
            metrics.export_metrics_to_csv(summary, filename=args.result_file)
        else:
            metrics.export_time_series(args.result_file)

if __name__ == "__main__":
    main()