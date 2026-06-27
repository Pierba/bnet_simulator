import argparse
from config.config_handler import ConfigHandler
import json
from multiprocessing import Pool
import os
import random
import subprocess
import time

# Arrange buoys randomly within the world boundaries, ensuring they are not too close to the edges
def arrange_buoys_randomly(n_buoys: int, world_width: float, world_height: float) -> list[tuple[float, float]]:
    positions: list[tuple[float, float]] = []
    for _ in range(n_buoys):
        x = random.uniform(10, world_width - 10)
        y = random.uniform(10, world_height - 10)
        positions.append((x, y))
    return positions

# Function to run a single simulation with given parameters
def run_simulation(
        mode: str, 
        interval: float, 
        density: float, 
        positions: list[tuple[float, float]], 
        results_dir: str, 
        cfg: ConfigHandler, 
        multihop_mode: str, 
        seed: int
):
    unique_id = f"{mode}_{multihop_mode}_{density}_{int(time.time() * 1000) % 10000}"
    positions_file = f"positions_{unique_id}.json"  # Name of simulation output file

    # Position written in json format
    with open(positions_file, "w") as f:
        json.dump(positions, f)
    
    # Determine the result file name based on the scenario and mode
    scenario = cfg.get('simulation', 'scenario')
    result_file = None
    match scenario:
        case 'static':
            result_file = os.path.join(results_dir, f"{mode}_static_density{density}.csv")
        case 'ramp':
            result_file = os.path.join(results_dir, f"{mode}_ramp_timeseries.csv")
        case 'random':
            result_file = os.path.join(results_dir, f"{mode}_random_density{density}.csv")

    # Calculate the number of mobile and fixed buoys based on the total and the mobile percentage
    total_buoys = len(positions)
    mobile_percentage = cfg.get('buoys', 'mobile_percentage')
    mobile_count = min(total_buoys, max(1, int(total_buoys * mobile_percentage))) if mobile_percentage > 0 else 0
    fixed_count = total_buoys - mobile_count

    # Set scheduler intervals
    min_interval: float = interval
    max_interval: float = cfg.get('scheduler', 'beacon_max_interval')

    # Build the command to run the simulation script with the appropriate arguments based on the configuration and parameters
    cmd = ["uv", "run", "src/script/init.py",
           "--mode", mode,
           "--seed", str(seed),
           "--world-width", str(cfg.get('world', 'width')),
           "--world-height", str(cfg.get('world', 'height')),
           "--mobile-buoy-count", str(mobile_count),
           "--fixed-buoy-count", str(fixed_count),
           "--duration", str(cfg.get('simulation', 'duration')),
           "--result-file", result_file,
           "--positions-file", positions_file,
           "--density", str(density),
           "--static-interval", str(interval),
           "--min-interval", str(min_interval),
           "--max-interval", str(max_interval),
           "--scenario", scenario,
           "--multihop-mode", multihop_mode,
        ]

    if cfg.get('simulation', 'ideal_channel'):
        cmd.append("--ideal")

    print(f"Running {mode} simulation ({multihop_mode} mode) with interval={interval}s and {density} density")

    # Run the simulation as a subprocess and wait for it to complete
    subprocess.run(cmd) # -> script/init.py
    
    # Clean up the positions file after the simulation is done
    if os.path.exists(positions_file):
        os.remove(positions_file)

# Subprocess worker function for parallel execution of simulations
def simulation_worker(args):
    mode, interval, density, positions, results_dir, cfg, multihop_mode, seed = args
    run_simulation(mode, interval, density, positions, results_dir, cfg, multihop_mode, seed)

# Dispatch simulations in parallel using multiprocessing Pool
def run_simulations_parallel(tasks: list[tuple], num_processes: int):
    with Pool(processes=num_processes) as pool:
        pool.map(simulation_worker, tasks)

# Plot results using the plot_metrics.py script
def plot_results(results_dir: str, plots_dir: str, interval: float, schedulers: list[str]):
    plot_cmd = ["uv", "run", "src/script/plot_metrics.py",
                "--results-dir", results_dir,
                "--plot-dir", plots_dir,
                "--interval", str(interval),
                "--schedulers", *schedulers]

    subprocess.run(plot_cmd) # -> script/plot_metrics.py

# Plot the cross-mode comparison histograms using the plot_mode_comparison.py script
def plot_mode_comparison(mode_results_dirs: dict[str, str], comparison_dir: str, interval: float, schedulers: list[str]):
    plot_cmd = ["uv", "run", "src/script/plot_mode_comparison.py",
                "--output-dir", comparison_dir,
                "--interval", str(interval),
                "--schedulers", *schedulers]
    for mode, results_dir in mode_results_dirs.items():
        plot_cmd.extend(("--mode-dir", mode, results_dir))

    subprocess.run(plot_cmd) # -> script/plot_mode_comparison.py

# Parse command line arguments to choose between a single run and a comparison sweep
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BNet simulator batch runner")
    parser.add_argument(
        "-n", "--no-plot",
        action="store_true",
        help="Only generate the result CSV files and skip all plotting. Useful when "
             "collecting many runs to average and plot together later.",
    )
    parser.add_argument(
        "-t", "--tag",
        type=str,
        default=None,
        help="Write this run's output under metrics/<tag>/ instead of metrics/. Lets "
             "repeated runs accumulate side by side (e.g. --tag run01) so they can be "
             "fed to avg_metrics.py as separate input dirs without overwriting.",
    )
    return parser.parse_args()

def main():
    args = parse_args()

    # Extracting configuration parameters
    cfg = ConfigHandler()

    # Root for this run's output; --tag nests it so repeated runs don't overwrite
    output_root = os.path.join("metrics", args.tag) if args.tag else "metrics"

    # Every simulation seed derives from it
    master_seed = int(time.time())
    random.seed(master_seed)
    print(f"Master seed: {master_seed}")

    #======================
    # PROTOCOLS 
    #======================
    schedulers: list[str] = cfg.get('simulation', 'schedulers')     # List of protocols to simulate

    #======================
    # BUOYS DISTRIBUTION 
    #======================
    min_buoys: int  = cfg.get('simulation', 'min_buoys')             # Minimum number of buoys to simulate
    max_buoys: int  = cfg.get('simulation', 'max_buoys')             # Maximum number of buoys to simulate
    step_buoys: int = cfg.get('simulation', 'step_buoys')           # Step size for buoy density

    #=======================
    # SIMULATION PARAMETERS
    #=======================
    intervals: list[float] = cfg.get('simulation', 'intervals')     # List of beacon intervals to simulate
    num_processes: int     = cfg.get('simulation', 'num_processes') # Number of parallel processes to use for parallel simulations
    ideal: bool            = cfg.get('simulation', 'ideal_channel') # Whether to simulate with an ideal channel (no collisions)
    scenario: str          = cfg.get('simulation', 'scenario')      # Scenario to run (static, ramp, random)
    world_width: float     = cfg.get('world', 'width')              # Width of the simulation world
    world_height: float    = cfg.get('world', 'height')             # Height of the simulation world
    
    # Multihop modes are swept like intervals and schedulers: one batch per mode.
    # When more than one is listed the cross-mode comparison plots are produced too.
    multihop_modes: list[str] = cfg.get('simulation', 'multihop_modes')
    print(f"Sweeping multihop modes: {multihop_modes}")

    try:
        # For each beacon interval: 
        # run the simulations for every multihop mode => plot per-mode results => plot the cross-mode comparison
        for interval in intervals: # [1.0, 0.5, 0.25]
            interval_str = f"{interval}"
            ideal_suffix = "_ideal" if ideal else ""
            scenario_suffix = f"_{scenario}"

            # Setting up the density values and buoy positions for the scenario
            densities = list(range(min_buoys, max_buoys + 1, step_buoys))
            if scenario == 'ramp':
                ramp_positions = arrange_buoys_randomly(max_buoys, world_width, world_height)
            else:
                positions_by_density = {
                    density: arrange_buoys_randomly(density, world_width, world_height)
                    for density in densities
                }

            # Track each mode's results directory to feed the comparison plotter
            mode_results_dirs: dict[str, str] = {}

            for multihop_mode in multihop_modes: # ['none', 'append', 'forwarded']
                multihop_suffix = f"_{multihop_mode}"

                results_dir = os.path.join(output_root, f"results_interval-{interval_str}{ideal_suffix}{scenario_suffix}{multihop_suffix}")
                os.makedirs(results_dir, exist_ok=True)

                if not args.no_plot:
                    plots_dir = os.path.join(output_root, f"plots_interval-{interval_str}{ideal_suffix}{scenario_suffix}{multihop_suffix}")
                    os.makedirs(plots_dir, exist_ok=True)

                print(f"Running simulations with interval = {interval}s, multihop mode = {multihop_mode}")

                match scenario:
                    case 'ramp':
                        for mode in schedulers: # ['static', 'dynamic_adab', 'dynamic_acab']
                            run_simulation(mode, interval, max_buoys, ramp_positions, results_dir, cfg, multihop_mode, random.randrange(2**32))

                    case 'random' | 'static':
                        tasks: list[tuple] = []
                        for density in densities:
                            positions = positions_by_density[density]
                            for mode in schedulers: # ['static', 'dynamic_adab', 'dynamic_acab']
                                # Each task is a tuple of arguments for the simulation_worker
                                # function, with its own distinct reproducible seed
                                tasks.append((mode, interval, density, positions, results_dir, cfg, multihop_mode, random.randrange(2**32)))

                        print(f"Running {len(tasks)} simulations in parallel using {num_processes} processes")
                        run_simulations_parallel(tasks, num_processes)

                if not args.no_plot:
                    print(f"Plotting results for interval = {interval}s, multihop mode = {multihop_mode}")
                    plot_results(results_dir, plots_dir, interval, schedulers)

                # Store the results directory for this mode to feed the comparison plotter later
                mode_results_dirs[multihop_mode] = results_dir

            # Build the cross-mode comparison histograms for density-based scenarios
            if not args.no_plot and scenario in ('random', 'static') and len(mode_results_dirs) > 1:
                comparison_dir = os.path.join(output_root, f"comparison_interval-{interval_str}{ideal_suffix}{scenario_suffix}")
                os.makedirs(comparison_dir, exist_ok=True)
                print(f"Plotting multihop mode comparison for interval = {interval}s")
                plot_mode_comparison(mode_results_dirs, comparison_dir, interval, schedulers)

        print("\nAll simulations complete!")
        print("Check the metrics directory for results and plots.")

        # Send a desktop notification when all simulations and plotting are done.
        # notify-send only exists on Linux; swallow its absence so the batch doesn't
        # crash at the very end on Windows/macOS (or headless Linux without libnotify).
        try:
            subprocess.run(["notify-send", "-e", "-i", "pycad", "-h", "string:sound-name:bell", "-a", "BNet Simulator", "Simulation Complete", "All simulations and plotting are done."])
        except (FileNotFoundError, OSError):
            pass
    
    # Handle keyboard interrupt to allow clean exit and cleanup of any leftover files
    except KeyboardInterrupt:
        print("\n\n[!] Simulation batch interrupted by user. Exiting cleanly...")
        
        # Clean up any leftover position files written by run_simulation
        for f in os.listdir('.'):
            if f.startswith('positions_') and f.endswith('.json'):
                try:
                    os.remove(f)
                except OSError as e:
                    print(f"Error removing file {f}: {e}")

if __name__ == "__main__":
    main()