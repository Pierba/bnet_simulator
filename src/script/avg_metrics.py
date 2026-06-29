import os
import pandas as pd
import numpy as np
import glob
import matplotlib.pyplot as plt
import re
from collections import defaultdict

from plot_mode_comparison import generate_comparison_plots

# Multihop modes that may appear as the trailing token of a results-dir name
MULTIHOP_MODES = ("none", "append", "forwarded")

def average_metrics(input_dirs, output_dir):
    # Identify subdirectories in each input directory
    all_subdirs = set()
    for input_dir in input_dirs:
        subdirs = [d for d in os.listdir(input_dir) 
                   if os.path.isdir(os.path.join(input_dir, d))]
        all_subdirs.update(subdirs)
    
    # Accumulate the averaged results dirs so modes that differ only by the
    # trailing multihop token can be compared once everything is averaged.
    comparison_groups: dict[str, dict[str, str]] = defaultdict(dict)  # group -> {mode: dir}
    group_interval: dict[str, float] = {}

    # Process each subdirectory separately
    for subdir in sorted(all_subdirs):
        # Mirror the input naming for the averaged outputs (strip the leading
        # "results_") so downstream tools recognise them identically, e.g.
        # results_interval-1.0_random_forwarded.
        suffix = subdir[len("results_"):] if subdir.startswith("results_") else subdir

        # Create results and plots directories directly in output_dir
        results_dir = os.path.join(output_dir, f"results_{suffix}")
        plots_dir = os.path.join(output_dir, f"plots_{suffix}")

        os.makedirs(results_dir, exist_ok=True)
        os.makedirs(plots_dir, exist_ok=True)

        # Collect input paths
        subdir_input_paths = [os.path.join(input_dir, subdir) for input_dir in input_dirs
                             if os.path.isdir(os.path.join(input_dir, subdir))]

        print(f"Processing {subdir}...")
        print(f"  Results will be saved to: {results_dir}")
        print(f"  Plots will be saved to: {plots_dir}")

        # Process density files (static_density*.csv, dynamic_density*.csv)
        process_density_files(subdir_input_paths, results_dir)

        # Process ramp timeseries files
        process_timeseries_files(subdir_input_paths, results_dir)

        # Process network-discovery growth side-car series (*_density*_discovery.csv)
        process_discovery_files(subdir_input_paths, results_dir)

        # Extract interval from subdirectory name for plotting
        interval = extract_interval_from_dirname(subdir)

        # Generate plots in the plot directory
        plot_averaged_metrics(results_dir, plots_dir, interval)

        # Register this averaged dir under its mode-agnostic group key
        for mode in MULTIHOP_MODES:
            if suffix.endswith(f"_{mode}"):
                group_key = suffix[: -len(f"_{mode}")]
                comparison_groups[group_key][mode] = results_dir
                group_interval[group_key] = interval
                break

    # Build the cross-mode comparison figures from the averaged dirs
    for group_key, mode_dirs in comparison_groups.items():
        if len(mode_dirs) < 2:
            continue
        comparison_dir = os.path.join(output_dir, f"comparison_{group_key}")
        print(f"Comparing multihop modes for {group_key}: {', '.join(mode_dirs)}")
        generate_comparison_plots(mode_dirs, comparison_dir, group_interval[group_key])

def extract_interval_from_dirname(dirname):
    """Pull the beacon interval (seconds) out of a results-dir name.

    Current convention encodes the literal value after a dash, e.g.
    "...interval-1.0..." or "...interval-0.25...". A legacy tenths-style
    encoding ("interval1" -> 1.0, "interval5" -> 0.5, "interval2_5" -> 0.25)
    is kept as a fallback for older directories.
    """
    # Current convention: literal seconds after a dash (interval-1.0, interval-0.25)
    match = re.search(r'interval-(\d+(?:\.\d+)?)', dirname)
    if match:
        return float(match.group(1))

    # Legacy tenths-style mappings
    if 'interval2_5' in dirname or 'interval2.5' in dirname:
        return 0.25
    if 'interval5' in dirname:
        return 0.5
    if 'interval1' in dirname:
        return 1.0

    # Fallback: try to parse from dirname if it doesn't match known patterns
    match = re.search(r'interval(\d+(?:_\d+)?)', dirname)
    if match:
        interval_str = match.group(1).replace('_', '.')
        try:
            return float(interval_str)
        except ValueError:
            return None

    return None

def process_density_files(input_dirs, output_dir):
    # Dictionary to store dataframes by file pattern
    all_data = {}
    
    # Collect all CSV files from input directories
    for input_dir in input_dirs:
        # The discovery side-car series (*_density*_discovery.csv) also match this
        # glob, but they are time-series rather than Metric/Value summaries; they
        # are averaged separately in process_discovery_files.
        csv_files = [f for f in glob.glob(os.path.join(input_dir, "*_density*.csv"))
                     if not f.endswith("_discovery.csv")]

        for csv_file in csv_files:
            # Extract base filename (e.g., "static_density10.csv")
            base_name = os.path.basename(csv_file)
            
            # Load the data
            df = pd.read_csv(csv_file, index_col=0)
            
            if base_name not in all_data:
                all_data[base_name] = []
            
            all_data[base_name].append(df)
    
    # Average the metrics for each file pattern
    for base_name, dataframes in all_data.items():
        # Extract metrics common to all dataframes
        common_metrics = set(dataframes[0].index)
        for df in dataframes[1:]:
            common_metrics &= set(df.index)
        
        # Create a new dataframe with averaged values
        avg_data = {}
        for metric in common_metrics:
            values = [df.loc[metric, "Value"] for df in dataframes]
            
            # Try to convert to numeric for averaging
            try:
                numeric_values = [float(v) for v in values]
                avg_value = sum(numeric_values) / len(numeric_values)
                std_dev = np.std(numeric_values)
                avg_data[metric] = {"Value": avg_value, "StdDev": std_dev}
            except (ValueError, TypeError):
                # For non-numeric values, use the most common one
                from collections import Counter
                most_common = Counter(values).most_common(1)[0][0]
                avg_data[metric] = {"Value": most_common, "StdDev": "N/A"}
        
        # Save averaged results
        avg_df = pd.DataFrame.from_dict(avg_data, orient="index")
        avg_df.to_csv(os.path.join(output_dir, base_name))

def process_timeseries_files(input_dirs, output_dir):
    # Dictionary to store timeseries data by mode
    timeseries_data = {"static": [], "dynamic_acab": [], "dynamic_adab": []}
    
    # Collect all timeseries CSV files
    for input_dir in input_dirs:
        for mode in timeseries_data.keys():
            ts_file = os.path.join(input_dir, f"{mode}_ramp_timeseries.csv")
            if os.path.exists(ts_file):
                df = pd.read_csv(ts_file)
                timeseries_data[mode].append(df)
    
    # Process each mode
    for mode, dataframes in timeseries_data.items():
        if not dataframes:
            continue
        
        # For timeseries, we need to align by time points
        # Get the set of time points from all dataframes
        all_times = sorted(set().union(*[set(df["time"]) for df in dataframes]))
        
        # Create a new dataframe with aligned time points
        avg_df = pd.DataFrame({"time": all_times})
        
        # Identify common columns across all dataframes
        common_cols = set(dataframes[0].columns)
        for df in dataframes[1:]:
            common_cols &= set(df.columns)
        
        # Remove 'time' from common columns
        if 'time' in common_cols:
            common_cols.remove('time')
        
        # For each dataframe, interpolate to match the aligned time points
        aligned_data = defaultdict(list)
        
        for df in dataframes:
            # Interpolate each column
            for col in common_cols:
                # Use nearest interpolation for categories or values that shouldn't be interpolated
                if col in ['n_buoys']:
                    interp_values = np.interp(all_times, df["time"], df[col], 
                                             left=df[col].iloc[0], right=df[col].iloc[-1])
                    interp_values = np.round(interp_values).astype(int)  # Round to nearest integer
                else:
                    interp_values = np.interp(all_times, df["time"], df[col], 
                                             left=np.nan, right=np.nan)
                
                aligned_data[col].append(interp_values)
        
        # Average across the interpolated dataframes
        for col in common_cols:
            values_array = np.array(aligned_data[col])
            avg_df[col] = np.nanmean(values_array, axis=0)
            avg_df[f"{col}_std"] = np.nanstd(values_array, axis=0)
        
        # Save the averaged timeseries
        avg_df.to_csv(os.path.join(output_dir, f"{mode}_ramp_timeseries.csv"), index=False)

def process_discovery_files(input_dirs, output_dir):
    """Seed-average the network-discovery growth series across runs.

    Each run drops one ``{scheduler}_..._density{D}_discovery.csv`` side-car per
    scheduler/density holding (time, avg_percentage_discovered). Runs sample at
    slightly different times, so we align them onto the union of time points by
    linear interpolation (mirroring process_timeseries_files) and write the mean
    plus a std column the comparison plotter renders as an error band.
    """
    # Group the per-run discovery dataframes by their (identical) file name
    all_data = defaultdict(list)
    for input_dir in input_dirs:
        for csv_file in glob.glob(os.path.join(input_dir, "*_discovery.csv")):
            df = pd.read_csv(csv_file)
            if "time" in df.columns and "avg_percentage_discovered" in df.columns and not df.empty:
                all_data[os.path.basename(csv_file)].append(df)

    for base_name, dataframes in all_data.items():
        # Align all runs onto the union of their sampled time points
        all_times = sorted(set().union(*[set(df["time"]) for df in dataframes]))

        aligned = []
        for df in dataframes:
            # Outside a run's own time span contributes NaN so it is ignored by nanmean
            aligned.append(np.interp(all_times, df["time"], df["avg_percentage_discovered"],
                                     left=np.nan, right=np.nan))

        values_array = np.array(aligned)
        avg_df = pd.DataFrame({"time": all_times})
        avg_df["avg_percentage_discovered"] = np.nanmean(values_array, axis=0)
        avg_df["avg_percentage_discovered_std"] = np.nanstd(values_array, axis=0)

        avg_df.to_csv(os.path.join(output_dir, base_name), index=False)

def plot_averaged_metrics(data_dir, plot_dir, interval=None):
    # Plot block by density with error bars
    plot_block_by_density_with_errors(data_dir, plot_dir, interval)
    
    # Plot unique nodes by density with error bars
    plot_unique_nodes_by_density_with_errors(data_dir, plot_dir, interval)
    
    # Plot timeseries with error bands
    plot_timeseries_with_errors(data_dir, plot_dir, interval)
    
    # Plot unique nodes timeseries with error bands
    plot_unique_nodes_vs_time_with_errors(data_dir, plot_dir, interval)
    
    # Plot ramp data grouped by buoy count
    plot_file = os.path.join(plot_dir, "b_pdr_grouped_by_buoy_count_ramp.png")
    plot_ramp_grouped_by_buoy_count_with_errors(data_dir, plot_file)

def get_density_dataframes(data_dir):
    files = glob.glob(os.path.join(data_dir, "*_density*.csv"))
    pdr_data = []
    collision_data = []
    loss_data = []
    unique_nodes_data = []
    
    # Extract data from CSV files
    for f in files:
        df = pd.read_csv(f, index_col=0)
        
        # Extract multihop mode
        multihop_mode = "none"
        if "Multihop Mode" in df.index:
            multihop_mode = str(df.loc["Multihop Mode", "Value"]).lower()
        
        # Determine scheduler type
        if "Scheduler Type" in df.index:
            sched_type = str(df.loc["Scheduler Type", "Value"]).lower()
        elif os.path.basename(f).startswith("static_"):
            sched_type = "static"
        elif os.path.basename(f).startswith("dynamic_acab_"):
            sched_type = "dynamic_acab"
        elif os.path.basename(f).startswith("dynamic_adab_"):
            sched_type = "dynamic_adab"
        elif os.path.basename(f).startswith("dynamic_"):
            sched_type = "dynamic_adab"
        else:
            sched_type = "unknown"
        
        # Extract per-copy PDR: each forward beacon counts as a normal beacon,
        # computed with the same formula as single-hop (actually_received / potentially_sent)
        if "Density" in df.index and "PDR" in df.index:
            density = float(df.loc["Density", "Value"])
            pdr = float(df.loc["PDR", "Value"])
            pdr_std = float(df.loc["PDR", "StdDev"])
            
            avg_neighbors = None
            if "Average Neighbors" in df.index:
                avg_neighbors = float(df.loc["Average Neighbors", "Value"])
            elif "Avg Neighbors" in df.index:
                avg_neighbors = float(df.loc["Avg Neighbors", "Value"])
            
            pdr_data.append((density, pdr, pdr_std, sched_type, avg_neighbors, multihop_mode))
        
        # Extract collision rate data
        if "Density" in df.index and "Collision Rate" in df.index:
            density = float(df.loc["Density", "Value"])
            collision_rate = float(df.loc["Collision Rate", "Value"])
            collision_std = float(df.loc["Collision Rate", "StdDev"])
            
            avg_neighbors = None
            if "Average Neighbors" in df.index:
                avg_neighbors = float(df.loc["Average Neighbors", "Value"])
            elif "Avg Neighbors" in df.index:
                avg_neighbors = float(df.loc["Avg Neighbors", "Value"])
            
            collision_data.append((density, collision_rate, collision_std, sched_type, avg_neighbors, multihop_mode))

        # Extract loss rate (total error) data
        if "Density" in df.index and "Loss Rate" in df.index:
            density = float(df.loc["Density", "Value"])
            loss_rate = float(df.loc["Loss Rate", "Value"])
            loss_std = float(df.loc["Loss Rate", "StdDev"])

            avg_neighbors = None
            if "Average Neighbors" in df.index:
                avg_neighbors = float(df.loc["Average Neighbors", "Value"])
            elif "Avg Neighbors" in df.index:
                avg_neighbors = float(df.loc["Avg Neighbors", "Value"])

            loss_data.append((density, loss_rate, loss_std, sched_type, avg_neighbors, multihop_mode))

        # Extract unique nodes data
        if "Density" in df.index and "Avg Unique Nodes Discovered" in df.index:
            density = float(df.loc["Density", "Value"])
            avg_unique = float(df.loc["Avg Unique Nodes Discovered", "Value"])
            avg_unique_std = float(df.loc["Avg Unique Nodes Discovered", "StdDev"])
            
            unique_nodes_data.append((density, avg_unique, avg_unique_std, sched_type, multihop_mode))
    
    # Create dataframes
    pdr_df = pd.DataFrame(pdr_data, columns=["Density", "PDR", "StdDev", "Scheduler", "AvgNeighbors", "MultihopMode"])
    coll_df = pd.DataFrame(collision_data, columns=["Density", "CollisionRate", "StdDev", "Scheduler", "AvgNeighbors", "MultihopMode"])
    loss_df = pd.DataFrame(loss_data, columns=["Density", "LossRate", "StdDev", "Scheduler", "AvgNeighbors", "MultihopMode"])
    unique_df = pd.DataFrame(unique_nodes_data, columns=["Density", "AvgUniqueNodes", "StdDev", "Scheduler", "MultihopMode"])

    return pdr_df, coll_df, loss_df, unique_df

def plot_block_by_density_with_errors(data_dir, plot_dir, interval=None):
    try:
        pdr_df, coll_df, loss_df, _ = get_density_dataframes(data_dir)
    except Exception as e:
        print(f"Error getting density data: {e}")
        return
    
    if pdr_df.empty:
        print("No PDR data with density found.")
        return
    
    # Get multihop mode for title
    multihop_modes = set(pdr_df["MultihopMode"].unique())
    mode_str = ""
    if len(multihop_modes) == 1:
        mode = list(multihop_modes)[0]
        if mode == "none":
            mode_str = "Single-Hop"
        elif mode == "append":
            mode_str = "Append Mode"
        elif mode == "forwarded":
            mode_str = "Forward Mode"
        else:
            mode_str = mode.capitalize()
    elif len(multihop_modes) > 1:
        mode_str = "Mixed Modes"
    
    # Create PDR by density plot with error bars
    densities = sorted(pdr_df["Density"].unique())
    schedulers = ["dynamic_acab", "dynamic_adab", "static"]
    scheduler_labels = {"static": "SBP", "dynamic_adab": "ADAB", "dynamic_acab": "ACAB"}
    color_map = {"static": "tab:blue", "dynamic_adab": "tab:orange", "dynamic_acab": "tab:green"}
    bar_width = 0.25
    x = np.arange(len(densities))
    
    fig, ax = plt.subplots(figsize=(10, 6))
    
    # Add second y-axis for average neighbors
    ax2 = ax.twinx()
    ax2.set_ylabel("Average Neighbors", color="black")
    ax2.tick_params(axis='y', labelcolor="black")
    ax2.grid(False)
    
    offset = -(len(schedulers) - 1) * bar_width / 2
    for i, sched in enumerate(schedulers):
        values = []
        errors = []
        for d in densities:
            rows = pdr_df[(pdr_df["Density"] == d) & (pdr_df["Scheduler"] == sched)]
            if not rows.empty:
                values.append(rows["PDR"].mean())
                errors.append(rows["StdDev"].mean())
            else:
                values.append(0)
                errors.append(0)
        
        values_arr = np.array(values)
        errors_arr = np.array(errors)
        lower_errors = np.minimum(errors_arr, values_arr)
        upper_errors = errors_arr
        
        ax.bar(x + offset + i * bar_width, values_arr, bar_width, 
               label=scheduler_labels[sched], color=color_map[sched])
        ax.errorbar(x + offset + i * bar_width, values_arr, 
                   yerr=[lower_errors, upper_errors], fmt='none', 
                   ecolor='black', capsize=5, alpha=0.7)
    
    # Plot average neighbors
    avg_neighbors_data = {}
    for d in densities:
        rows = pdr_df[pdr_df["Density"] == d]
        if not rows.empty and not rows["AvgNeighbors"].isna().all():
            avg_neighbors_data[d] = rows["AvgNeighbors"].mean()
    
    if avg_neighbors_data:
        density_points = []
        neighbor_values = []
        
        for d in densities:
            if d in avg_neighbors_data:
                density_points.append(d)
                neighbor_values.append(avg_neighbors_data[d])
        
        if density_points:
            x_positions = [list(densities).index(d) for d in density_points]
            
            ax2.plot(x_positions, neighbor_values, color='black', marker='o', 
                   linestyle='-', linewidth=1, label='Avg Neighbors')
            
            for i, (x_pos, value) in enumerate(zip(x_positions, neighbor_values)):
                ax2.text(x_pos, value, f"{value:.1f}", color='black', 
                       ha='center', va='bottom', fontsize=8)
            
            max_avg_neighbors = max(neighbor_values)
            ax2.set_ylim(0, max_avg_neighbors * 1.2)
    
    ax.set_xlabel("Total Buoys")
    ax.set_ylabel("PDR")
    ax.set_ylim(0, 1)

    # Update title to include mode
    title_parts = ["PDR vs Buoy Count"]
    if mode_str:
        title_parts.append(f"({mode_str}")
        if interval:
            title_parts.append(f", Static Interval: {interval}s)")
        else:
            title_parts.append(")")
    elif interval:
        title_parts.append(f"(Static Interval: {interval}s)")
    ax.set_title(" ".join(title_parts))
    
    ax.set_xticks(x)
    ax.set_xticklabels([str(int(d)) for d in densities])
    
    lines1, labels1 = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines1 + lines2, labels1 + labels2, loc='lower right')
    ax.grid(axis="y", linestyle="--", alpha=0.6)
    
    plt.tight_layout()
    
    if interval:
        plt.savefig(os.path.join(plot_dir, f"b_pdr_interval{int(interval*10)}.png"))
    else:
        plt.savefig(os.path.join(plot_dir, "b_pdr_block_by_density.png"))
    plt.close()
    
    # Create collision rate by density plot with error bars
    if not coll_df.empty:
        fig, ax = plt.subplots(figsize=(10, 6))

        offset = -(len(schedulers) - 1) * bar_width / 2
        for i, sched in enumerate(schedulers):
            values = []
            errors = []
            for d in densities:
                rows = coll_df[(coll_df["Density"] == d) & (coll_df["Scheduler"] == sched)]
                if not rows.empty:
                    values.append(rows["CollisionRate"].mean())
                    errors.append(rows["StdDev"].mean())
                else:
                    values.append(0)
                    errors.append(0)

            values_arr = np.array(values)
            errors_arr = np.array(errors)
            lower_errors = np.minimum(errors_arr, values_arr)
            upper_errors = errors_arr

            ax.bar(x + offset + i * bar_width, values_arr, bar_width,
                   label=scheduler_labels[sched], color=color_map[sched])
            ax.errorbar(x + offset + i * bar_width, values_arr,
                       yerr=[lower_errors, upper_errors], fmt='none',
                       ecolor='black', capsize=5, alpha=0.7)

        ax.set_xlabel("Total Buoys")
        ax.set_ylabel("Collision Rate")
        ax.set_ylim(0, 1)

        # Update title to include mode
        title_parts = ["Collision Rate vs Buoy Count"]
        if mode_str:
            title_parts.append(f"({mode_str}")
            if interval:
                title_parts.append(f", Static Interval: {interval}s)")
            else:
                title_parts.append(")")
        elif interval:
            title_parts.append(f"(Static Interval: {interval}s)")
        ax.set_title(" ".join(title_parts))

        ax.set_xticks(x)
        ax.set_xticklabels([str(int(d)) for d in densities])
        ax.legend(loc='upper left')
        ax.grid(axis="y", linestyle="--", alpha=0.6)

        plt.tight_layout()

        if interval:
            plt.savefig(os.path.join(plot_dir, f"collision_rate_interval{int(interval*10)}.png"))
        else:
            plt.savefig(os.path.join(plot_dir, "collision_rate_block_by_density.png"))
        plt.close()
    else:
        print("No collision rate data with density found.")

    # Create loss rate (total error) by density plot with error bars
    if not loss_df.empty:
        fig, ax = plt.subplots(figsize=(10, 6))

        offset = -(len(schedulers) - 1) * bar_width / 2
        for i, sched in enumerate(schedulers):
            values = []
            errors = []
            for d in densities:
                rows = loss_df[(loss_df["Density"] == d) & (loss_df["Scheduler"] == sched)]
                if not rows.empty:
                    values.append(rows["LossRate"].mean())
                    errors.append(rows["StdDev"].mean())
                else:
                    values.append(0)
                    errors.append(0)

            values_arr = np.array(values)
            errors_arr = np.array(errors)
            lower_errors = np.minimum(errors_arr, values_arr)
            upper_errors = errors_arr

            ax.bar(x + offset + i * bar_width, values_arr, bar_width,
                   label=scheduler_labels[sched], color=color_map[sched])
            ax.errorbar(x + offset + i * bar_width, values_arr,
                       yerr=[lower_errors, upper_errors], fmt='none',
                       ecolor='black', capsize=5, alpha=0.7)

        ax.set_xlabel("Total Buoys")
        ax.set_ylabel("Loss Rate")
        ax.set_ylim(0, 1)

        # Update title to include mode
        title_parts = ["Loss Rate vs Buoy Count"]
        if mode_str:
            title_parts.append(f"({mode_str}")
            if interval:
                title_parts.append(f", Static Interval: {interval}s)")
            else:
                title_parts.append(")")
        elif interval:
            title_parts.append(f"(Static Interval: {interval}s)")
        ax.set_title(" ".join(title_parts))

        ax.set_xticks(x)
        ax.set_xticklabels([str(int(d)) for d in densities])
        ax.legend(loc='upper left')
        ax.grid(axis="y", linestyle="--", alpha=0.6)

        plt.tight_layout()

        if interval:
            plt.savefig(os.path.join(plot_dir, f"loss_rate_interval{int(interval*10)}.png"))
        else:
            plt.savefig(os.path.join(plot_dir, "loss_rate_block_by_density.png"))
        plt.close()
    else:
        print("No loss rate data with density found.")

def plot_unique_nodes_by_density_with_errors(data_dir, plot_dir, interval=None):
    """Plot average unique nodes discovered (as percentage) vs density with error bars"""
    try:
        _, _, _, unique_df = get_density_dataframes(data_dir)
    except Exception as e:
        print(f"Error getting unique nodes data: {e}")
        return
    
    if unique_df.empty:
        print("No unique nodes data with density found.")
        return
    
    # Calculate percentage
    unique_df["PercentageDiscovered"] = (unique_df["AvgUniqueNodes"] / (unique_df["Density"] - 1)) * 100
    unique_df["PercentageStdDev"] = (unique_df["StdDev"] / (unique_df["Density"] - 1)) * 100
    
    # Get multihop mode for title
    multihop_modes = set(unique_df["MultihopMode"].unique())
    mode_str = ""
    if len(multihop_modes) == 1:
        mode = list(multihop_modes)[0]
        if mode == "none":
            mode_str = "Single-Hop"
        elif mode == "append":
            mode_str = "Append Mode"
        elif mode == "forwarded":
            mode_str = "Forward Mode"
        else:
            mode_str = mode.capitalize()
    elif len(multihop_modes) > 1:
        mode_str = "Mixed Modes"
    
    densities = sorted(unique_df["Density"].unique())
    schedulers = ["dynamic_acab", "dynamic_adab", "static"]
    scheduler_labels = {"static": "SBP", "dynamic_adab": "ADAB", "dynamic_acab": "ACAB"}
    color_map = {"static": "tab:blue", "dynamic_adab": "tab:orange", "dynamic_acab": "tab:green"}
    bar_width = 0.25
    x = np.arange(len(densities))
    
    fig, ax = plt.subplots(figsize=(10, 6))
    
    offset = -(len(schedulers) - 1) * bar_width / 2
    for i, sched in enumerate(schedulers):
        values = []
        errors = []
        for d in densities:
            rows = unique_df[(unique_df["Density"] == d) & (unique_df["Scheduler"] == sched)]
            if not rows.empty:
                values.append(rows["PercentageDiscovered"].mean())
                errors.append(rows["PercentageStdDev"].mean())
            else:
                values.append(0)
                errors.append(0)
        
        values_arr = np.array(values)
        errors_arr = np.array(errors)
        lower_errors = np.minimum(errors_arr, values_arr)
        upper_errors = np.minimum(errors_arr, 100 - values_arr)
        
        ax.bar(x + offset + i * bar_width, values_arr, bar_width, 
               label=scheduler_labels[sched], color=color_map[sched])
        ax.errorbar(x + offset + i * bar_width, values_arr, 
                   yerr=[lower_errors, upper_errors], fmt='none', 
                   ecolor='black', capsize=5, alpha=0.7)
    
    ax.set_xlabel("Total Buoys")
    ax.set_ylabel("Avg % of Network Discovered")
    ax.set_ylim(0, 100)
    
    # Update title to include mode
    title_parts = ["Avg % of Network Discovered vs Buoy Count"]
    if mode_str:
        title_parts.append(f"({mode_str}")
        if interval:
            title_parts.append(f", Static Interval: {interval}s)")
        else:
            title_parts.append(")")
    elif interval:
        title_parts.append(f"(Static Interval: {interval}s)")
    ax.set_title(" ".join(title_parts))
    
    ax.set_xticks(x)
    ax.set_xticklabels([str(int(d)) for d in densities])
    ax.legend(loc='upper left')
    ax.grid(axis="y", linestyle="--", alpha=0.6)
    
    plt.tight_layout()
    
    if interval:
        plt.savefig(os.path.join(plot_dir, f"avg_percentage_network_discovered_interval{int(interval*10)}.png"))
    else:
        plt.savefig(os.path.join(plot_dir, "avg_percentage_network_discovered_by_density.png"))
    plt.close()

def plot_ramp_grouped_by_buoy_count_with_errors(data_dir, plot_file):
    modes = [("dynamic_acab", "tab:green"), ("dynamic_adab", "tab:orange"), ("static", "tab:blue")]
    
    min_buoys = float('inf')
    max_buoys = 0
    all_data = {}
    multihop_mode = None
    
    for mode, _ in modes:
        csv_file = os.path.join(data_dir, f"{mode}_ramp_timeseries.csv")
        if os.path.exists(csv_file):
            df = pd.read_csv(csv_file)
            if "n_buoys" in df.columns:
                min_buoys = min(min_buoys, df["n_buoys"].min())
                max_buoys = max(max_buoys, df["n_buoys"].max())
                all_data[mode] = df
            
            main_csv = csv_file.replace("_timeseries.csv", ".csv")
            if multihop_mode is None and os.path.exists(main_csv):
                main_df = pd.read_csv(main_csv, index_col=0)
                if "Multihop Mode" in main_df.index:
                    multihop_mode = str(main_df.loc["Multihop Mode", "Value"]).lower()
    
    if not all_data:
        print("No valid data files found for buoy count grouping")
        return
    
    buoy_range = max_buoys - min_buoys
    if buoy_range <= 10:
        num_groups = max(5, buoy_range)
    else:
        num_groups = min(10, max(5, buoy_range // 5))
    
    group_edges = np.linspace(min_buoys, max_buoys + 1, num_groups + 1).astype(int)
    group_edges[0] = int(min_buoys)
    group_labels = [f"{group_edges[i]}-{group_edges[i+1]-1}" for i in range(len(group_edges)-1)]
    
    grouped_data = {}
    grouped_std = {}
    valid_modes = []
    
    for mode, color in modes:
        if mode in all_data:
            df = all_data[mode]
            
            if "pdr" in df.columns:
                y_col = "pdr"
                std_col = "pdr_std"
            elif "delivery_ratio" in df.columns:
                y_col = "delivery_ratio"
                std_col = "delivery_ratio_std"
            else:
                print(f"Warning: No pdr or delivery_ratio column in data for {mode}")
                continue

            df["group"] = pd.cut(df["n_buoys"], bins=group_edges, labels=group_labels, right=False)
            grouped = df.groupby("group", observed=False)[y_col].mean().reindex(group_labels)
            
            if std_col in df.columns:
                grouped_errors = df.groupby("group", observed=False)[std_col].mean().reindex(group_labels)
            else:
                grouped_errors = pd.Series(0, index=group_labels)
            
            if not grouped.empty and len(grouped.values) > 0:
                grouped_data[mode] = grouped.values
                grouped_std[mode] = grouped_errors.values
                valid_modes.append((mode, color))
            else:
                print(f"Warning: No valid grouped data for {mode}")
    
    if not valid_modes:
        print("No valid data to plot for any mode")
        return
    
    x = np.arange(len(group_labels))
    bar_width = 0.25
    fig, ax = plt.subplots(figsize=(10, 6))
    
    mode_labels = {"static": "SBP", "dynamic_adab": "ADAB", "dynamic_acab": "ACAB"}
    offset = -(len(valid_modes) - 1) * bar_width / 2
    
    for i, (mode, color) in enumerate(valid_modes):
        data = grouped_data[mode]
        errors = grouped_std[mode]
        
        if len(data) == len(x):
            data_arr = np.array(data)
            errors_arr = np.array(errors)
            lower_errors = np.minimum(errors_arr, data_arr)
            upper_errors = errors_arr
            
            label = mode_labels.get(mode, mode.capitalize())
            ax.bar(x + offset + i * bar_width, data_arr, bar_width, 
                  label=label, color=color)
            
            if np.any(errors_arr > 0):
                ax.errorbar(x + offset + i * bar_width, data_arr, 
                           yerr=[lower_errors, upper_errors], fmt='none', 
                           ecolor='black', capsize=5, alpha=0.7)
        else:
            print(f"Warning: Data length mismatch for {mode}. Expected {len(x)}, got {len(data)}")
    
    ax.set_xlabel("Buoy Count Group")
    ax.set_ylabel("Average PDR")
    ax.set_ylim(0, 1)

    # Update title to include mode
    title = "Average PDR vs Buoy Count Group (Ramp Scenario"
    if multihop_mode:
        if multihop_mode == "none":
            title += ", Single-Hop)"
        elif multihop_mode == "append":
            title += ", Append Mode)"
        elif multihop_mode == "forwarded":
            title += ", Forward Mode)"
        else:
            title += f", {multihop_mode.capitalize()})"
    else:
        title += ")"
    ax.set_title(title)
    
    ax.set_xticks(x)
    ax.set_xticklabels(group_labels)
    ax.legend(loc="lower right")
    ax.grid(axis="y", linestyle="--", alpha=0.6)
    plt.tight_layout()
    plt.savefig(plot_file)
    plt.close()

def plot_timeseries_with_errors(data_dir, plot_dir, interval=None):
    modes = [("dynamic_acab", "tab:green"), ("dynamic_adab", "tab:orange"), ("static", "tab:blue")]
    mode_labels = {"static": "SBP", "dynamic_adab": "ADAB", "dynamic_acab": "ACAB"}
    plt.figure(figsize=(10, 6))
    found = False

    time_buoy = None
    max_buoys = 0
    time_neighbors = None
    multihop_mode = None

    for mode, color in modes:
        csv_file = os.path.join(data_dir, f"{mode}_ramp_timeseries.csv")
        if os.path.exists(csv_file):
            df = pd.read_csv(csv_file)
            if "pdr" in df.columns:
                y_col = "pdr"
                std_col = "pdr_std"
            elif "delivery_ratio" in df.columns:
                y_col = "delivery_ratio"
                std_col = "delivery_ratio_std"
            else:
                print(f"Warning: No pdr or delivery_ratio column in {csv_file}")
                continue

            label = mode_labels.get(mode, mode.capitalize())
            plt.plot(df["time"], df[y_col], label=label, color=color)
            
            if std_col in df.columns:
                lower_bound = np.maximum(df[y_col] - df[std_col], 0)
                upper_bound = df[y_col] + df[std_col]
                
                plt.fill_between(
                    df["time"],
                    lower_bound,
                    upper_bound,
                    color=color, alpha=0.2
                )
            
            found = True
            if time_buoy is None and "n_buoys" in df.columns:
                time_buoy = (df["time"], df["n_buoys"])
                max_buoys = df["n_buoys"].max()
            
            if time_neighbors is None:
                if "avg_neighbors" in df.columns:
                    time_neighbors = (df["time"], df["avg_neighbors"])
                elif "average_neighbors" in df.columns:
                    time_neighbors = (df["time"], df["average_neighbors"])
            
            main_csv = csv_file.replace("_timeseries.csv", ".csv")
            if multihop_mode is None and os.path.exists(main_csv):
                main_df = pd.read_csv(main_csv, index_col=0)
                if "Multihop Mode" in main_df.index:
                    multihop_mode = str(main_df.loc["Multihop Mode", "Value"]).lower()

    if not found:
        print("No ramp timeseries files found for plotting.")
        return

    ax = plt.gca()
    handles, labels = ax.get_legend_handles_labels()

    if time_buoy is not None:
        ax2 = ax.twinx()
        gray_area = ax2.fill_between(time_buoy[0], time_buoy[1], color="gray", alpha=0.2, label="Buoy Count")
        ax2.set_ylabel("Buoy Count", fontsize=12)
        ax2.set_ylim(0, max(40, int(max_buoys)))
        ax2.tick_params(axis='y', colors='gray')
        ax2.grid(False)
        handles += [gray_area]
        labels += ["Buoy Count"]
        
        if time_neighbors is not None:
            ax3 = ax.twinx()
            ax3.spines["right"].set_position(("axes", 1.1))
            neighbor_line = ax3.plot(time_neighbors[0], time_neighbors[1], 
                                   color="black", linestyle="-", linewidth=1, label="Avg. Neighbors")
            ax3.set_ylabel("Avg. Neighbors", color="black", fontsize=12)
            ax3.tick_params(axis='y', colors='black')
            ax3.grid(False)
            handles += neighbor_line
            labels += ["Avg. Neighbors"]

    ax.set_xlabel("Time (s)", fontsize=12)
    ax.set_ylabel("PDR", fontsize=12)
    ax.set_ylim(0, 1)

    # Update title to include mode
    title_parts = ["PDR vs Time (Ramp Scenario"]
    if multihop_mode:
        if multihop_mode == "none":
            title_parts.append(", Single-Hop")
        elif multihop_mode == "append":
            title_parts.append(", Append Mode")
        elif multihop_mode == "forwarded":
            title_parts.append(", Forward Mode")
        else:
            title_parts.append(f", {multihop_mode.capitalize()}")
    
    if interval:
        title_parts.append(f", Static Interval: {interval}s)")
    else:
        title_parts.append(")")
    
    plt.title("".join(title_parts))

    ax.legend(handles, labels, loc="lower right", fontsize=11)
    ax.grid(True)
    plt.tight_layout()
    
    plt.savefig(os.path.join(plot_dir, "b_pdr_vs_time_ramp.png"))
    plt.close()

def plot_unique_nodes_vs_time_with_errors(data_dir, plot_dir, interval=None):
    """Plot average unique nodes discovered vs time with error bands"""
    modes = [("dynamic_acab", "tab:green"), ("dynamic_adab", "tab:orange"), ("static", "tab:blue")]
    mode_labels = {"static": "SBP", "dynamic_adab": "ADAB", "dynamic_acab": "ACAB"}
    plt.figure(figsize=(10, 6))
    found = False

    time_buoy = None
    max_buoys = 0
    multihop_mode = None

    for mode, color in modes:
        csv_file = os.path.join(data_dir, f"{mode}_ramp_timeseries.csv")
        if os.path.exists(csv_file):
            df = pd.read_csv(csv_file)
            if "avg_unique_nodes" in df.columns:
                label = mode_labels.get(mode, mode.capitalize())
                plt.plot(df["time"], df["avg_unique_nodes"], label=label, color=color)
                
                if "avg_unique_nodes_std" in df.columns:
                    lower_bound = np.maximum(df["avg_unique_nodes"] - df["avg_unique_nodes_std"], 0)
                    upper_bound = df["avg_unique_nodes"] + df["avg_unique_nodes_std"]
                    
                    plt.fill_between(
                        df["time"],
                        lower_bound,
                        upper_bound,
                        color=color, alpha=0.2
                    )
                
                found = True
                
                if time_buoy is None and "n_buoys" in df.columns:
                    time_buoy = (df["time"], df["n_buoys"])
                    max_buoys = df["n_buoys"].max()
            
            main_csv = csv_file.replace("_timeseries.csv", ".csv")
            if multihop_mode is None and os.path.exists(main_csv):
                main_df = pd.read_csv(main_csv, index_col=0)
                if "Multihop Mode" in main_df.index:
                    multihop_mode = str(main_df.loc["Multihop Mode", "Value"]).lower()

    if not found:
        print("No ramp timeseries files with avg_unique_nodes found for plotting.")
        return

    ax = plt.gca()
    handles, labels = ax.get_legend_handles_labels()

    if time_buoy is not None:
        ax2 = ax.twinx()
        gray_area = ax2.fill_between(time_buoy[0], time_buoy[1], color="gray", alpha=0.2, label="Buoy Count")
        ax2.set_ylabel("Buoy Count", fontsize=12)
        ax2.set_ylim(0, max(40, int(max_buoys)))
        ax2.tick_params(axis='y', colors='gray')
        ax2.grid(False)
        handles += [gray_area]
        labels += ["Buoy Count"]

    ax.set_xlabel("Time (s)", fontsize=12)
    ax.set_ylabel("Avg Unique Nodes Discovered", fontsize=12)
    
    # Update title to include mode
    title_parts = ["Avg Unique Nodes Discovered vs Time (Ramp Scenario"]
    if multihop_mode:
        if multihop_mode == "none":
            title_parts.append(", Single-Hop")
        elif multihop_mode == "append":
            title_parts.append(", Append Mode")
        elif multihop_mode == "forwarded":
            title_parts.append(", Forward Mode")
        else:
            title_parts.append(f", {multihop_mode.capitalize()}")
    
    if interval:
        title_parts.append(f", Static Interval: {interval}s)")
    else:
        title_parts.append(")")
    
    plt.title("".join(title_parts))

    ax.legend(handles, labels, loc="lower right", fontsize=11)
    ax.grid(True)
    plt.tight_layout()
    
    plt.savefig(os.path.join(plot_dir, "avg_unique_nodes_vs_time_ramp.png"))
    plt.close()

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Average metrics across multiple simulation runs")
    parser.add_argument("--input-dirs", nargs="+", required=True, help="Input metrics directories")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    
    args = parser.parse_args()
    
    os.makedirs(args.output_dir, exist_ok=True)
    
    print(f"Input directories: {args.input_dirs}")
    print(f"Output directory: {args.output_dir}")
    
    average_metrics(args.input_dirs, args.output_dir)
    print(f"Metrics averaging complete.")

if __name__ == "__main__":
    main()