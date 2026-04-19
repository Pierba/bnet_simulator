import os
import json
import subprocess
import time
import random
from multiprocessing import Pool
from config.config_handler import ConfigHandler

# Arrange buoys randomly within the world boundaries, ensuring they are not too close to the edges
def arrange_buoys_randomly(n_buoys, world_width, world_height):
    positions = []
    random.seed(time.time())
    for _ in range(n_buoys):
        x = random.uniform(10, world_width - 10)
        y = random.uniform(10, world_height - 10)
        positions.append((x, y))
    return positions

# Function to run a single simulation with given parameters
def run_simulation(mode, interval, density, positions, results_dir, cfg):
    unique_id = f"{mode}_{density}_{int(time.time() * 1000) % 10000}"
    positions_file = f"positions_{unique_id}.json"  # Name of simulation output file
    
    # Position written in json format
    with open(positions_file, "w") as f:
        json.dump(positions, f)
    
    ramp = cfg.get('simulation', 'ramp_scenario')
    if ramp:
        result_file = os.path.join(results_dir, f"{mode}_ramp_timeseries.csv")
    else:
        result_file = os.path.join(results_dir, f"{mode}_density{density}.csv")
    
    total_buoys = len(positions)
    mobile_percentage = cfg.get('buoys', 'mobile_percentage')

    # Calculate the number of mobile and fixed buoys based on the total and the mobile percentage
    mobile_count = min(total_buoys, max(1, int(total_buoys * mobile_percentage))) if mobile_percentage > 0 else 0
    fixed_count = total_buoys - mobile_count

    # Build the command to run the simulation script with the appropriate arguments based on the configuration and parameters
    cmd = ["uv", "run", "src/script/init.py",
           "--mode", mode,
           "--seed", str(int(time.time())),
           "--world-width", str(cfg.get('world', 'width')),
           "--world-height", str(cfg.get('world', 'height')),
           "--mobile-buoy-count", str(mobile_count),
           "--fixed-buoy-count", str(fixed_count),
           "--duration", str(cfg.get('simulation', 'duration')),
           "--result-file", result_file,
           "--positions-file", positions_file,
           "--density", str(density),
           "--static-interval", str(interval)]
    
    if ramp:
        cmd.append("--ramp")
    if cfg.get('simulation', 'ideal_channel'):
        cmd.append("--ideal")
    
    print(f"Running {mode} simulation with interval={interval}s and {density} density")

    # Run the simulation as a subprocess and wait for it to complete
    subprocess.run(cmd) # -> script/init.py
    
    # Clean up the positions file after the simulation is done
    if os.path.exists(positions_file):
        os.remove(positions_file)

# Subpocesses worker function for parallel execution of simulations
def simulation_worker(args):
    mode, interval, density, positions, results_dir, cfg = args
    run_simulation(mode, interval, density, positions, results_dir, cfg)

# Dispatch simulations in parallel using multiprocessing Pool
def run_simulations_parallel(tasks, num_processes):
    with Pool(processes=num_processes) as pool:
        pool.map(simulation_worker, tasks)

def plot_results(results_dir, plots_dir, interval):
    plot_cmd = ["uv", "run", "src/script/plot_metrics.py",
                "--results-dir", results_dir,
                "--plot-dir", plots_dir,
                "--interval", str(interval)]
    subprocess.run(plot_cmd)

# Naming system for results and plots directories based on the interval value.
def get_interval_str(interval: float) -> str:
    interval_str = None
    if interval < 1:
        interval_str = str(int(interval * 100))
        if interval * 100 % 10 == 0: # 0.5
            interval_str = str(int(interval * 10))
        else: # 0.25
            interval_str = f"{int(interval * 10)}_{int(interval * 100) % 10}"
    else:
        interval_str = str(int(interval))
    return interval_str

def main():
    cfg = ConfigHandler()
    
    # Extracting configuration parameters

    #======================
    # PROTOCOLS 
    #======================
    schedulers = cfg.get('simulation', 'schedulers')    # List of protocols to simulate

    #======================
    # BUOYS DISTRIBUTION 
    #======================
    min_buoys = cfg.get('simulation', 'min_buoys')      # Minimum number of buoys to simulate
    max_buoys = cfg.get('simulation', 'max_buoys')      # Maximum number of buoys to simulate
    step_buoys = cfg.get('simulation', 'step_buoys')    # Step size for buoy density
    
    #=======================
    # SIMULATION PARAMETERS
    #=======================
    intervals: list[float] = cfg.get('simulation', 'intervals')     # List of beacon intervals to simulate
    num_processes: int = cfg.get('simulation', 'num_processes')     # Number of parallel processes to use for parallel simulations
    ideal: bool = cfg.get('simulation', 'ideal_channel')            # Whether to simulate with an ideal channel (no collisions)
    ramp: bool = cfg.get('simulation', 'ramp_scenario')             # Whether to run the ramp scenario (increasing density over time)
    world_width: float = cfg.get('world', 'width')                  # Width of the simulation world
    world_height: float = cfg.get('world', 'height')                # Height of the simulation world
    
    
    for interval in intervals: # [1.0, 0.5, 0.25]
        interval_str = get_interval_str(interval)
        ideal_suffix = "_ideal" if ideal else ""
        ramp_suffix = "_ramp" if ramp else ""
        
        results_dir = os.path.join("metrics", f"results_interval{interval_str}{ideal_suffix}{ramp_suffix}")
        plots_dir = os.path.join("metrics", f"plots_interval{interval_str}{ideal_suffix}{ramp_suffix}")
        os.makedirs(results_dir, exist_ok=True)
        os.makedirs(plots_dir, exist_ok=True)
        
        print(f"Running simulations with interval = {interval}s")
        
        if ramp:
            positions = arrange_buoys_randomly(max_buoys, world_width, world_height)
            for mode in schedulers: # ['static', 'dynamic_adab', 'dynamic_acab']
                run_simulation(mode, interval, max_buoys, positions, results_dir, cfg)
        else:
            # Density of buoys within the specified range and step size
            tasks = []
            densities = list(range(min_buoys, max_buoys + 1, step_buoys))
            for density in densities:
                positions = arrange_buoys_randomly(density, world_width, world_height)
                for mode in schedulers: # ['static', 'dynamic_adab', 'dynamic_acab'] -> protocols
                    # Each task is a tuple of arguments for the simulation_worker function
                    tasks.append((mode, interval, density, positions, results_dir, cfg))
            
            print(f"Running {len(tasks)} simulations in parallel using {num_processes} processes")
            run_simulations_parallel(tasks, num_processes)
        
        print(f"Plotting results for interval = {interval}s")
        plot_results(results_dir, plots_dir, interval)
        
    print("\nAll simulations complete!")
    print("Check the metrics directory for results and plots.")

    subprocess.run(["notify-send", "-e", "-i", "pycad", "-h", "string:sound-name:bell", "-a", "BNet Simulator", "Simulation Complete", "All simulations and plotting are done."])

if __name__ == "__main__":
    main()