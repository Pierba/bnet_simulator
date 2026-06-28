import os
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import re

# Schedulers plotted by default when --schedulers is not provided.
DEFAULT_SCHEDULERS = ["dynamic_acab", "dynamic_adab", "static"]
SCHEDULER_LABELS = {"static": "SBP", "dynamic_adab": "ADAB", "dynamic_acab": "ACAB"}
SCHEDULER_COLORS = {"static": "tab:blue", "dynamic_adab": "tab:orange", "dynamic_acab": "tab:green"}

def resample_timeseries(df, time_col="time", num_points=200):
    """Resample a time series DataFrame to a fixed number of evenly-spaced points.
    
    Deduplicates timestamps (keeping last value) and interpolates to create
    uniform time sampling across all protocols.
    """
    # Drop duplicate timestamps, keeping the last value at each time
    df = df.drop_duplicates(subset=[time_col], keep="last").sort_values(time_col).reset_index(drop=True)
    
    if len(df) <= num_points:
        return df
    
    # Create evenly-spaced time points
    t_min, t_max = df[time_col].min(), df[time_col].max()
    new_times = np.linspace(t_min, t_max, num_points)
    
    # Interpolate each numeric column at the new time points
    result = pd.DataFrame({time_col: new_times})
    for col in df.columns:
        if col == time_col:
            continue
        result[col] = np.interp(new_times, df[time_col].values, df[col].values)
    
    return result

def plot_block_by_density(results_dir, plot_dir, interval=None, schedulers=None):
    schedulers = schedulers or DEFAULT_SCHEDULERS
    files = [f for f in os.listdir(results_dir) if f.endswith(".csv")]
    data = []
    collision_data = []
    loss_data = []
    avg_neighbors_data = {}
    multihop_modes = set()  # Track multihop modes
    
    # Extract data from CSV files
    for f in files:
        df = pd.read_csv(os.path.join(results_dir, f), index_col=0)
        if "Density" in df.index and "PDR" in df.index:
            density = float(df.loc["Density", "Value"])
            pdr = float(df.loc["PDR", "Value"])
            
            if "Average Neighbors" in df.index:
                avg_neighbors = float(df.loc["Average Neighbors", "Value"])
                avg_neighbors_data[density] = avg_neighbors
            
            # Extract multihop mode
            if "Multihop Mode" in df.index:
                mode = str(df.loc["Multihop Mode", "Value"]).lower()
                multihop_modes.add(mode)
            
            # Determine scheduler type
            if "Scheduler Type" in df.index:
                sched_type = str(df.loc["Scheduler Type", "Value"]).lower()
            elif f.startswith("static_"):
                sched_type = "static"
            elif f.startswith("dynamic_acab_"):
                sched_type = "dynamic_acab"
            elif f.startswith("dynamic_adab_"):
                sched_type = "dynamic_adab"
            elif f.startswith("dynamic_"):
                sched_type = "dynamic_adab"
            else:
                sched_type = "unknown"
                
            data.append((density, pdr, sched_type))
            
        if "Density" in df.index and "Collision Rate" in df.index:
            density = float(df.loc["Density", "Value"])
            collision_rate = float(df.loc["Collision Rate", "Value"])
            
            if "Scheduler Type" in df.index:
                sched_type = str(df.loc["Scheduler Type", "Value"]).lower()
            elif f.startswith("static_"):
                sched_type = "static"
            elif f.startswith("dynamic_acab_"):
                sched_type = "dynamic_acab"
            elif f.startswith("dynamic_adab_"):
                sched_type = "dynamic_adab"
            elif f.startswith("dynamic_"):
                sched_type = "dynamic_adab"
            else:
                sched_type = "unknown"

            collision_data.append((density, collision_rate, sched_type))

        if "Density" in df.index and "Loss Rate" in df.index:
            density = float(df.loc["Density", "Value"])
            loss_rate = float(df.loc["Loss Rate", "Value"])

            if "Scheduler Type" in df.index:
                sched_type = str(df.loc["Scheduler Type", "Value"]).lower()
            elif f.startswith("static_"):
                sched_type = "static"
            elif f.startswith("dynamic_acab_"):
                sched_type = "dynamic_acab"
            elif f.startswith("dynamic_adab_"):
                sched_type = "dynamic_adab"
            elif f.startswith("dynamic_"):
                sched_type = "dynamic_adab"
            else:
                sched_type = "unknown"

            loss_data.append((density, loss_rate, sched_type))

    if not data:
        print("No PDR data with density found.")
        return
    
    # Determine mode string for title
    mode_str = ""
    if multihop_modes:
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
        else:
            mode_str = "Mixed Modes"
    
    # Create PDR by density plot
    df = pd.DataFrame(data, columns=["Density", "PDR", "Scheduler"])
    grouped = df.groupby(["Density", "Scheduler"], observed=False).mean().reset_index()
    densities = sorted(df["Density"].unique())
    bar_width = 0.8 / max(1, len(schedulers))
    x = np.arange(len(densities))
    
    fig, ax = plt.subplots(figsize=(10, 6))
    
    ax2 = ax.twinx()
    ax2.set_ylabel("Average Neighbors", color="black")
    ax2.tick_params(axis='y', labelcolor="black")
    ax2.grid(False)
    
    offset = -(len(schedulers) - 1) * bar_width / 2
    for i, sched in enumerate(schedulers):
        pdrs = []
        for d in densities:
            row = grouped[(grouped["Density"] == d) & (grouped["Scheduler"] == sched)]
            pdrs.append(row["PDR"].values[0] if not row.empty else 0)
        ax.bar(x + offset + i * bar_width, pdrs, bar_width, label=SCHEDULER_LABELS[sched], color=SCHEDULER_COLORS[sched])
    
    # Plot average neighbors as a connected line across all densities
    if avg_neighbors_data:
        # Prepare data points for the line
        density_points = []
        neighbor_values = []
        
        # Collect data points in order of density
        for d in densities:
            if d in avg_neighbors_data:
                density_points.append(d)
                neighbor_values.append(avg_neighbors_data[d])
        
        # Only proceed if we have points to plot
        if density_points:
            # Convert density values to x-positions for plotting
            x_positions = [list(densities).index(d) for d in density_points]
            
            # Plot the connected line
            ax2.plot(x_positions, neighbor_values, color='black', marker='o', 
                   linestyle='-', linewidth=1, label='Avg Neighbors')
            
            # Add text labels at each point
            for i, (x_pos, value) in enumerate(zip(x_positions, neighbor_values)):
                ax2.text(x_pos, value, f"{value:.1f}", color='black', 
                       ha='center', va='bottom', fontsize=8)
            
            # Set y-limits for average neighbors axis
            max_avg_neighbors = max(neighbor_values)
            ax2.set_ylim(0, max_avg_neighbors * 1.2)
    
    ax.set_xlabel("Total Buoys")
    ax.set_ylabel("PDR")

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
        plt.savefig(os.path.join(plot_dir, f"pdr_interval{int(interval*10)}.png"))
    else:
        plt.savefig(os.path.join(plot_dir, "pdr_block_by_density.png"))
    plt.close()

    # Create collision rate by density plot
    if collision_data:
        coll_df = pd.DataFrame(collision_data, columns=["Density", "CollisionRate", "Scheduler"])
        grouped_coll = coll_df.groupby(["Density", "Scheduler"], observed=False).mean().reset_index()
        densities = sorted(coll_df["Density"].unique())

        fig, ax = plt.subplots(figsize=(10, 6))
        offset = -(len(schedulers) - 1) * bar_width / 2
        for i, sched in enumerate(schedulers):
            rates = []
            for d in densities:
                row = grouped_coll[(grouped_coll["Density"] == d) & (grouped_coll["Scheduler"] == sched)]
                rates.append(row["CollisionRate"].values[0] if not row.empty else 0)
            ax.bar(x + offset + i * bar_width, rates, bar_width, label=SCHEDULER_LABELS[sched], color=SCHEDULER_COLORS[sched])

        ax.set_xlabel("Total Buoys")
        ax.set_ylabel("Collision Rate")

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
        ax.legend()
        ax.grid(axis="y", linestyle="--", alpha=0.6)
        plt.tight_layout()

        if interval:
            plt.savefig(os.path.join(plot_dir, f"collision_rate_interval{int(interval*10)}.png"))
        else:
            plt.savefig(os.path.join(plot_dir, "collision_rate_block_by_density.png"))
        plt.close()
    else:
        print("No collision rate data with density found.")

    # Create loss rate (total error) by density plot
    if loss_data:
        loss_df = pd.DataFrame(loss_data, columns=["Density", "LossRate", "Scheduler"])
        grouped_loss = loss_df.groupby(["Density", "Scheduler"], observed=False).mean().reset_index()
        densities = sorted(loss_df["Density"].unique())

        fig, ax = plt.subplots(figsize=(10, 6))
        offset = -(len(schedulers) - 1) * bar_width / 2
        for i, sched in enumerate(schedulers):
            rates = []
            for d in densities:
                row = grouped_loss[(grouped_loss["Density"] == d) & (grouped_loss["Scheduler"] == sched)]
                rates.append(row["LossRate"].values[0] if not row.empty else 0)
            ax.bar(x + offset + i * bar_width, rates, bar_width, label=SCHEDULER_LABELS[sched], color=SCHEDULER_COLORS[sched])

        ax.set_xlabel("Total Buoys")
        ax.set_ylabel("Loss Rate")

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
        ax.legend()
        ax.grid(axis="y", linestyle="--", alpha=0.6)
        plt.tight_layout()

        if interval:
            plt.savefig(os.path.join(plot_dir, f"loss_rate_interval{int(interval*10)}.png"))
        else:
            plt.savefig(os.path.join(plot_dir, "loss_rate_block_by_density.png"))
        plt.close()
    else:
        print("No loss rate data with density found.")

def plot_ramp_grouped_by_buoy_count(results_dir, plot_file, schedulers=None):
    schedulers = schedulers or DEFAULT_SCHEDULERS
    modes = [(sched, SCHEDULER_COLORS[sched]) for sched in schedulers]

    min_buoys = float('inf')
    max_buoys = 0
    all_data = {}
    multihop_mode = None  # Track multihop mode
    
    for mode, _ in modes:
        csv_file = os.path.join(results_dir, f"{mode}_ramp_timeseries.csv")
        if os.path.exists(csv_file):
            df = pd.read_csv(csv_file)
            if "n_buoys" in df.columns:
                min_buoys = min(min_buoys, df["n_buoys"].min())
                max_buoys = max(max_buoys, df["n_buoys"].max())
                all_data[mode] = df
            
            # Try to get multihop mode from main CSV (not timeseries)
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
    # If bin edges are not unique (e.g., min==max), create a single group covering the whole range
    unique_edges = np.unique(group_edges)
    if len(unique_edges) <= 2:
        group_labels = [f"{int(min_buoys)}-{int(max_buoys)}"]
        single_group = True
    else:
        group_labels = [f"{group_edges[i]}-{group_edges[i+1]-1}" for i in range(len(group_edges)-1)]
        single_group = False
    
    # Build a mapping mode -> {label: value} and collect all labels across modes
    grouped_map = {}
    all_labels = set()
    valid_modes = []

    for mode, color in modes:
        if mode not in all_data:
            continue
        df = all_data[mode]

        if "pdr" in df.columns:
            y_col = "pdr"
        elif "delivery_ratio" in df.columns:
            y_col = "delivery_ratio"
        else:
            print(f"Warning: No pdr or delivery_ratio column in data for {mode}")
            continue

        label_values = {}
        if single_group:
            label = group_labels[0]
            label_values[label] = df[y_col].mean()
            all_labels.add(label)
        else:
            # Bin without providing labels; derive labels from intervals present
            binned = pd.cut(df["n_buoys"], bins=group_edges, right=False, duplicates='drop')
            df2 = df.copy()
            df2["group"] = binned
            grp = df2.groupby("group", observed=False)[y_col].mean()
            for interval, val in grp.items():
                if pd.isna(interval):
                    continue
                left = int(interval.left)
                right = int(interval.right) - 1
                label = f"{left}-{right}"
                label_values[label] = val
                all_labels.add(label)

        if label_values:
            grouped_map[mode] = label_values
            valid_modes.append((mode, color))
        else:
            print(f"Warning: No valid grouped data for {mode}")

    if not valid_modes:
        print("No valid data to plot for any mode")
        return

    # Create a sorted list of all labels by numeric lower bound
    def label_key(lbl):
        parts = lbl.split('-')
        try:
            return int(parts[0])
        except Exception:
            return 0

    group_labels = sorted(list(all_labels), key=label_key)
    grouped_data = {}
    for mode, color in valid_modes:
        values = [grouped_map.get(mode, {}).get(lbl, 0) for lbl in group_labels]
        grouped_data[mode] = values

    x = np.arange(len(group_labels))
    bar_width = 0.8 / max(1, len(valid_modes))
    fig, ax = plt.subplots(figsize=(10, 6))

    offset = -(len(valid_modes) - 1) * bar_width / 2

    for i, (mode, color) in enumerate(valid_modes):
        data = grouped_data[mode]
        if len(data) == len(x):
            ax.bar(x + offset + i * bar_width, data, bar_width,
                  label=SCHEDULER_LABELS[mode], color=color)
        else:
            print(f"Warning: Data length mismatch for {mode}. Expected {len(x)}, got {len(data)}")
    
    ax.set_xlabel("Buoy Count Group")
    ax.set_ylabel("Average PDR")

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

def extract_interval_from_dirname(dirname):
    """Pull the beacon interval (seconds) out of a results-dir name.

    Kept byte-for-byte in sync with the canonical version in
    ``avg_metrics.extract_interval_from_dirname`` so the two tools never disagree.
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

def plot_delivery_ratio_vs_time(results_dir, plot_file, interval=None, schedulers=None):
    schedulers = schedulers or DEFAULT_SCHEDULERS
    modes = [(sched, SCHEDULER_COLORS[sched]) for sched in schedulers]
    plt.figure(figsize=(10, 6))
    found = False

    time_buoy = None
    max_buoys = 0
    time_neighbors = None
    multihop_mode = None  # Track multihop mode

    for mode, color in modes:
        csv_file = os.path.join(results_dir, f"{mode}_ramp_timeseries.csv")
        if os.path.exists(csv_file):
            df = pd.read_csv(csv_file)
            if "pdr" in df.columns:
                y_col = "pdr"
            elif "delivery_ratio" in df.columns:
                y_col = "delivery_ratio"
            else:
                print(f"Warning: No pdr or delivery_ratio column in {csv_file}")
                continue
            label = SCHEDULER_LABELS[mode]
            df_resampled = resample_timeseries(df, time_col="time")
            plt.plot(df_resampled["time"], df_resampled[y_col], label=label, color=color)
            found = True
            
            if time_buoy is None and "n_buoys" in df_resampled.columns:
                time_buoy = (df_resampled["time"], df_resampled["n_buoys"])
                max_buoys = df_resampled["n_buoys"].max()
            
            if time_neighbors is None and "avg_neighbors" in df_resampled.columns:
                time_neighbors = (df_resampled["time"], df_resampled["avg_neighbors"])
            
            # Try to get multihop mode from main CSV
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
        handles2, labels2 = ax2.get_legend_handles_labels()
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
    plt.savefig(plot_file)
    plt.close()

def plot_unique_nodes_by_density(results_dir, plot_dir, interval=None, schedulers=None):
    """Plot average unique nodes discovered vs density for different schedulers"""
    schedulers = schedulers or DEFAULT_SCHEDULERS
    files = [f for f in os.listdir(results_dir) if f.endswith(".csv")]
    data = []
    
    for f in files:
        df = pd.read_csv(os.path.join(results_dir, f), index_col=0)
        if "Density" in df.index and "Avg Unique Nodes Discovered" in df.index:
            density = float(df.loc["Density", "Value"])
            avg_unique = float(df.loc["Avg Unique Nodes Discovered", "Value"])
            
            avg_neighbors = float(df.loc["Average Neighbors", "Value"]) if "Average Neighbors" in df.index else 0
            
            if "Scheduler Type" in df.index:
                sched_type = str(df.loc["Scheduler Type", "Value"]).lower()
            elif f.startswith("static_"):
                sched_type = "static"
            elif f.startswith("dynamic_acab_"):
                sched_type = "dynamic_acab"
            elif f.startswith("dynamic_adab_"):
                sched_type = "dynamic_adab"
            elif f.startswith("dynamic_"):
                sched_type = "dynamic_adab"
            else:
                sched_type = "unknown"
            
            multihop_mode = "none"
            if "Multihop Mode" in df.index:
                multihop_mode = str(df.loc["Multihop Mode", "Value"]).lower()
                
            data.append((density, avg_unique, avg_neighbors, sched_type, multihop_mode))
    
    if not data:
        print("No unique nodes data with density found.")
        return
    
    df = pd.DataFrame(data, columns=["Density", "AvgUniqueNodes", "AvgNeighbors", "Scheduler", "MultihopMode"])
    
    # Calculate percentage - (avg_unique / (density - 1)) * 100
    # density - 1 because we exclude self from potential discoveries
    df["PercentageDiscovered"] = (df["AvgUniqueNodes"] / (df["Density"] - 1)) * 100
    
    grouped = df.groupby(["Density", "Scheduler", "MultihopMode"], observed=False).mean().reset_index()

    densities = sorted(df["Density"].unique())

    multihop_modes = sorted(df["MultihopMode"].unique())

    if len(multihop_modes) > 1:
        fig, axes = plt.subplots(1, len(multihop_modes), figsize=(8 * len(multihop_modes), 6))
        if len(multihop_modes) == 1:
            axes = [axes]

        for ax, mode in zip(axes, multihop_modes):
            mode_data = grouped[grouped["MultihopMode"] == mode]
            bar_width = 0.8 / max(1, len(schedulers))
            x = np.arange(len(densities))

            offset = -(len(schedulers) - 1) * bar_width / 2
            for i, sched in enumerate(schedulers):
                values = []
                for d in densities:
                    row = mode_data[(mode_data["Density"] == d) & (mode_data["Scheduler"] == sched)]
                    values.append(row["PercentageDiscovered"].values[0] if not row.empty else 0)  # UPDATED
                ax.bar(x + offset + i * bar_width, values, bar_width,
                      label=SCHEDULER_LABELS[sched], color=SCHEDULER_COLORS[sched])
            
            ax.set_xlabel("Total Buoys")
            ax.set_ylabel("Avg % of Network Discovered")  # UPDATED
            ax.set_ylim(0, 100)  # Set y-axis from 0 to 100%
            mode_title = mode.capitalize() if mode != "none" else "Single-Hop"
            ax.set_title(f"{mode_title} Mode")
            ax.set_xticks(x)
            ax.set_xticklabels([str(int(d)) for d in densities])
            ax.legend(loc='upper left')
            ax.grid(axis="y", linestyle="--", alpha=0.6)
        
        if interval:
            fig.suptitle(f"Avg % of Network Discovered vs Buoy Count (Static Interval: {interval}s)")  # UPDATED
        else:
            fig.suptitle("Avg % of Network Discovered vs Buoy Count")  # UPDATED
        plt.tight_layout()
        
    else:
        fig, ax = plt.subplots(figsize=(10, 6))
        bar_width = 0.8 / max(1, len(schedulers))
        x = np.arange(len(densities))

        offset = -(len(schedulers) - 1) * bar_width / 2
        for i, sched in enumerate(schedulers):
            values = []
            for d in densities:
                row = grouped[(grouped["Density"] == d) & (grouped["Scheduler"] == sched)]
                values.append(row["PercentageDiscovered"].values[0] if not row.empty else 0)  # UPDATED
            ax.bar(x + offset + i * bar_width, values, bar_width,
                  label=SCHEDULER_LABELS[sched], color=SCHEDULER_COLORS[sched])
        
        ax.set_xlabel("Total Buoys")
        ax.set_ylabel("Avg % of Network Discovered")  # UPDATED
        ax.set_ylim(0, 100)  # Set y-axis from 0 to 100%
        mode = multihop_modes[0]
        mode_title = mode.capitalize() if mode != "none" else "Single-Hop"
        
        title_parts = ["Avg % of Network Discovered vs Buoy Count"]  # UPDATED
        title_parts.append(f"({mode_title} Mode")
        if interval:
            title_parts.append(f", Static Interval: {interval}s)")
        else:
            title_parts.append(")")
        ax.set_title(" ".join(title_parts))
        
        ax.set_xticks(x)
        ax.set_xticklabels([str(int(d)) for d in densities])
        ax.legend(loc='upper left')
        ax.grid(axis="y", linestyle="--", alpha=0.6)
        plt.tight_layout()
    
    if interval:
        plt.savefig(os.path.join(plot_dir, f"avg_percentage_network_discovered_interval{int(interval*10)}.png"))  # UPDATED
    else:
        plt.savefig(os.path.join(plot_dir, "avg_percentage_network_discovered_by_density.png"))  # UPDATED
    plt.close()

def plot_unique_nodes_vs_time(results_dir, plot_file, interval=None, schedulers=None):
    """Plot average unique nodes discovered vs time for ramp scenarios"""
    schedulers = schedulers or DEFAULT_SCHEDULERS
    modes = [(sched, SCHEDULER_COLORS[sched]) for sched in schedulers]
    plt.figure(figsize=(10, 6))
    found = False

    time_buoy = None
    max_buoys = 0
    multihop_mode = None  # Track multihop mode

    for mode, color in modes:
        csv_file = os.path.join(results_dir, f"{mode}_ramp_timeseries.csv")
        if os.path.exists(csv_file):
            df = pd.read_csv(csv_file)
            if "avg_unique_nodes" in df.columns:
                label = SCHEDULER_LABELS[mode]
                df_resampled = resample_timeseries(df, time_col="time")
                plt.plot(df_resampled["time"], df_resampled["avg_unique_nodes"], label=label, color=color)
                found = True
                
                if time_buoy is None and "n_buoys" in df_resampled.columns:
                    time_buoy = (df_resampled["time"], df_resampled["n_buoys"])
                    max_buoys = df_resampled["n_buoys"].max()

            # Try to get multihop mode from main CSV
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
        handles2, labels2 = ax2.get_legend_handles_labels()
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
    plt.savefig(plot_file)
    plt.close()

def plot_network_discovery_growth(results_dir, plot_file, interval=None, schedulers=None):
    """Plot how the avg % of network discovered grows over time, for the densest run.

    For density-based scenarios (static/random) every run drops a
    ``{mode}_..._density{D}_discovery.csv`` side-car series. We pick, per scheduler,
    the file with the largest density (the last value of the densities sweep) and
    plot its discovery growth curve.
    """
    schedulers = schedulers or DEFAULT_SCHEDULERS
    files = [f for f in os.listdir(results_dir) if f.endswith("_discovery.csv")]
    if not files:
        print("No discovery time-series files found for plotting.")
        return

    plt.figure(figsize=(10, 6))
    found = False
    max_density = None
    multihop_mode = None

    for mode in schedulers:
        # Collect this scheduler's discovery files and the density encoded in each name
        candidates = []
        for f in files:
            if not f.startswith(f"{mode}_"):
                continue
            m = re.search(r"density(\d+(?:\.\d+)?)_discovery\.csv$", f)
            if m:
                candidates.append((float(m.group(1)), f))

        if not candidates:
            continue

        # Densest run = last value of the densities sweep
        density, densest_file = max(candidates, key=lambda c: c[0])
        df = pd.read_csv(os.path.join(results_dir, densest_file))
        if "time" not in df.columns or "avg_percentage_discovered" not in df.columns or df.empty:
            continue

        plt.plot(df["time"], df["avg_percentage_discovered"],
                 label=SCHEDULER_LABELS.get(mode, mode), color=SCHEDULER_COLORS.get(mode, None))
        found = True
        max_density = density if max_density is None else max(max_density, density)

        # Pull the multihop mode from the matching summary CSV (drop the _discovery suffix)
        if multihop_mode is None:
            main_csv = densest_file.replace("_discovery.csv", ".csv")
            main_path = os.path.join(results_dir, main_csv)
            if os.path.exists(main_path):
                main_df = pd.read_csv(main_path, index_col=0)
                if "Multihop Mode" in main_df.index:
                    multihop_mode = str(main_df.loc["Multihop Mode", "Value"]).lower()

    if not found:
        print("No valid discovery time-series data to plot.")
        plt.close()
        return

    ax = plt.gca()
    ax.set_xlabel("Time (s)", fontsize=12)
    ax.set_ylabel("Avg % of Network Discovered", fontsize=12)
    ax.set_ylim(0, 100)

    density_str = f" — {int(max_density)} Buoys" if max_density is not None else ""
    title_parts = [f"Network Discovery Growth (Densest Run{density_str}"]
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

    ax.legend(loc="lower right", fontsize=11)
    ax.grid(True)
    plt.tight_layout()
    plt.savefig(plot_file)
    plt.close()

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", type=str, default=None, help="Directory with result CSVs")
    parser.add_argument("--plot-dir", type=str, default=None, help="Directory to save plots")
    parser.add_argument("--interval", type=float, default=None, help="Static interval value to display in plot")
    parser.add_argument("--schedulers", nargs="+", default=None,
                        help=f"Schedulers to plot (default: {' '.join(DEFAULT_SCHEDULERS)})")
    args = parser.parse_args()

    results_dir = args.results_dir or os.environ.get("RESULTS_DIR", "test_results")
    plot_dir = args.plot_dir or os.environ.get("PLOT_DIR", "test_plots")
    schedulers = args.schedulers or DEFAULT_SCHEDULERS

    interval = args.interval
    if interval is None:
        interval = extract_interval_from_dirname(results_dir)
    else:
        interval = float(interval)
        
    print(f"Loading results from: {results_dir}")
    print(f"Saving plots to: {plot_dir}")
    if interval:
        print(f"Using static interval: {interval}s")

    if not os.path.exists(plot_dir):
        os.makedirs(plot_dir, exist_ok=True)

    print("Plotting standard metrics...")
    plot_block_by_density(results_dir, plot_dir, interval=interval, schedulers=schedulers)

    print("Plotting unique nodes by density...")
    plot_unique_nodes_by_density(results_dir, plot_dir, interval=interval, schedulers=schedulers)

    print("Plotting PDR vs time for ramp scenarios...")
    plot_file = os.path.join(plot_dir, "b_pdr_vs_time_ramp.png")
    plot_delivery_ratio_vs_time(results_dir, plot_file, interval=interval, schedulers=schedulers)

    print("Plotting unique nodes vs time for ramp scenarios...")
    plot_file = os.path.join(plot_dir, "avg_unique_nodes_vs_time_ramp.png")
    plot_unique_nodes_vs_time(results_dir, plot_file, interval=interval, schedulers=schedulers)

    print("Plotting network discovery growth over time for densest run...")
    plot_file = os.path.join(plot_dir, "network_discovery_growth_densest.png")
    plot_network_discovery_growth(results_dir, plot_file, interval=interval, schedulers=schedulers)

    print("Plotting PDR grouped by buoy count for ramp scenario...")
    plot_group_file = os.path.join(plot_dir, "b_pdr_grouped_by_buoy_count_ramp.png")
    plot_ramp_grouped_by_buoy_count(results_dir, plot_group_file, schedulers=schedulers)

    print("Plots saved to:", plot_dir)

if __name__ == "__main__":
    main()