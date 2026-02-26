import os
import sys
import json
import subprocess
import time
import random
from multiprocessing import Pool
from config.config_handler import ConfigHandler

def arrange_buoys_randomly(n_buoys, world_width, world_height):
    positions = []
    random.seed(time.time())
    for _ in range(n_buoys):
        x = random.uniform(10, world_width - 10)
        y = random.uniform(10, world_height - 10)
        positions.append((x, y))
    return positions

def run_simulation(mode, interval, density, positions, results_dir, cfg):
    unique_id = f"{mode}_{density}_{int(time.time() * 1000) % 10000}"
    positions_file = f"positions_{unique_id}.json"
    with open(positions_file, "w") as f:
        json.dump(positions, f)
    
    ramp = cfg.get('simulation', 'ramp_scenario')
    if ramp:
        result_file = os.path.join(results_dir, f"{mode}_ramp_timeseries.csv")
    else:
        result_file = os.path.join(results_dir, f"{mode}_density{density}.csv")
    
    mobile = cfg.get('buoys', 'mobile_percentage') > 0
    if mobile:
        total_buoys = len(positions)
        mobile_percentage = cfg.get('buoys', 'mobile_percentage')
        mobile_count = max(1, int(total_buoys * mobile_percentage))
        fixed_count = total_buoys - mobile_count
    else:
        mobile_count = 0
        fixed_count = len(positions)
    
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
    subprocess.run(cmd)
    
    if os.path.exists(positions_file):
        os.remove(positions_file)

def simulation_worker(args):
    mode, interval, density, positions, results_dir, cfg = args
    run_simulation(mode, interval, density, positions, results_dir, cfg)

def run_simulations_parallel(tasks, num_processes):
    with Pool(processes=num_processes) as pool:
        pool.map(simulation_worker, tasks)

def plot_results(results_dir, plots_dir, interval):
    plot_cmd = ["uv", "run", "src/script/plot_metrics.py",
                "--results-dir", results_dir,
                "--plot-dir", plots_dir,
                "--interval", str(interval)]
    subprocess.run(plot_cmd)

def main():
    cfg = ConfigHandler()
    
    schedulers = cfg.get('simulation', 'schedulers')
    min_buoys = cfg.get('simulation', 'min_buoys')
    max_buoys = cfg.get('simulation', 'max_buoys')
    step_buoys = cfg.get('simulation', 'step_buoys')
    intervals = cfg.get('simulation', 'intervals')
    num_processes = cfg.get('simulation', 'num_processes')
    ideal = cfg.get('simulation', 'ideal_channel')
    ramp = cfg.get('simulation', 'ramp_scenario')
    world_width = cfg.get('world', 'width')
    world_height = cfg.get('world', 'height')
    
    densities = list(range(min_buoys, max_buoys + 1, step_buoys))
    
    for interval in intervals:
        if interval < 1:
            interval_str = str(int(interval * 100))
            if interval * 100 % 10 == 0:
                interval_str = str(int(interval * 10))
            else:
                interval_str = f"{int(interval * 10)}_{int(interval * 100) % 10}"
        else:
            interval_str = str(int(interval))
            
        ideal_suffix = "_ideal" if ideal else ""
        ramp_suffix = "_ramp" if ramp else ""
        
        results_dir = os.path.join("metrics", f"results_interval{interval_str}{ideal_suffix}{ramp_suffix}")
        plots_dir = os.path.join("metrics", f"plots_interval{interval_str}{ideal_suffix}{ramp_suffix}")
        
        os.makedirs(results_dir, exist_ok=True)
        os.makedirs(plots_dir, exist_ok=True)
        
        print(f"Running simulations with interval = {interval}s")
        
        if ramp:
            positions = arrange_buoys_randomly(max_buoys, world_width, world_height)
            for mode in schedulers:
                run_simulation(mode, interval, max_buoys, positions, results_dir, cfg)
        else:
            tasks = []
            for density in densities:
                positions = arrange_buoys_randomly(density, world_width, world_height)
                for mode in schedulers:
                    tasks.append((mode, interval, density, positions, results_dir, cfg))
            
            print(f"Running {len(tasks)} simulations in parallel using {num_processes} processes")
            run_simulations_parallel(tasks, num_processes)
        
        print(f"Plotting results for interval = {interval}s")
        plot_results(results_dir, plots_dir, interval)
        
    print("\nAll simulations complete!")
    print("Check the metrics directory for results and plots.")
    if sys.platform != "win32":
        subprocess.run([
            "notify-send",
            "-e",
            "-i", "pycad",
            "-h", "string:sound-name:bell",
            "-a", "BNet Simulator",
            "Simulation Complete",
            "All simulations and plotting are done."
        ])

if __name__ == "__main__":
    main()