import os
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import re
import glob

def extract_interval_from_csv(csv_file):
    """Extract static interval from a CSV file"""
    try:
        df = pd.read_csv(csv_file, index_col=0)
        if "Static Interval" in df.index:
            interval_value = df.loc["Static Interval", "Value"]
            if interval_value != "N/A":
                return float(interval_value)
    except Exception as e:
        print(f"Warning: Could not extract interval from {csv_file}: {e}")
    return None

def extract_interval_from_dirname(dirname):
    """
    Hard-coded interval mapping based on directory naming convention.
    interval1_ideal -> 1.0s
    interval2_5_ideal -> 0.25s  
    interval5_ideal -> 0.5s
    """
    # Hard-coded mappings
    if 'interval1' in dirname:
        return 1.0
    elif 'interval2_5' in dirname or 'interval2.5' in dirname:
        return 0.25
    elif 'interval5' in dirname:
        return 0.5
    
    # Fallback: try to parse from dirname if it doesn't match known patterns
    match = re.search(r'interval(\d+(?:_\d+)?)', dirname)
    if match:
        interval_str = match.group(1).replace('_', '.')
        try:
            return float(interval_str)
        except ValueError:
            return None
    
    return None

def find_common_intervals(base_dirs):
    """
    Find intervals that exist in all three mode directories.
    Returns a dict mapping interval values to their suffix strings.
    """
    intervals_by_mode = {}
    
    # Collect intervals from each mode directory
    for mode_name, base_dir in base_dirs.items():
        if not os.path.exists(base_dir):
            print(f"Warning: Directory {base_dir} does not exist")
            continue
        
        subdirs = [d for d in os.listdir(base_dir) if d.startswith("results_")]
        mode_intervals = {}
        
        for subdir in subdirs:
            interval_suffix = subdir.replace("results_", "")
            results_dir = os.path.join(base_dir, subdir)
            
            # Try to get interval from any CSV file in this directory
            csv_files = glob.glob(os.path.join(results_dir, "*.csv"))
            interval_value = None
            
            for csv_file in csv_files:
                interval_value = extract_interval_from_csv(csv_file)
                if interval_value is not None:
                    break
            
            # Fallback to directory name parsing if CSV doesn't have it
            if interval_value is None:
                interval_value = extract_interval_from_dirname(interval_suffix)
            
            if interval_value is not None:
                mode_intervals[interval_value] = interval_suffix
        
        intervals_by_mode[mode_name] = mode_intervals
    
    if not intervals_by_mode:
        return {}
    
    # Find common intervals across all modes
    all_mode_names = list(intervals_by_mode.keys())
    if not all_mode_names:
        return {}
    
    common_interval_values = set(intervals_by_mode[all_mode_names[0]].keys())
    
    for mode_name in all_mode_names[1:]:
        common_interval_values &= set(intervals_by_mode[mode_name].keys())
    
    # Build result dict mapping interval value to suffix
    # Use the suffix from the first mode (they should all be the same)
    result = {}
    first_mode = all_mode_names[0]
    for interval_value in common_interval_values:
        result[interval_value] = intervals_by_mode[first_mode][interval_value]
    
    return result

def get_density_dataframes_by_mode(base_dirs, interval_suffix):
    """
    Collect data from all three modes (none, append, forward) for a given interval
    
    Args:
        base_dirs: dict with keys 'none', 'append', 'forward' pointing to base directories
        interval_suffix: e.g., "interval1_ideal"
    
    Returns:
        DataFrame with columns: Density, B-PDR, StdDev, Scheduler, MultihopMode
    """
    all_data = []
    
    for mode_name, base_dir in base_dirs.items():
        results_dir = os.path.join(base_dir, f"results_{interval_suffix}")
        
        if not os.path.exists(results_dir):
            print(f"Warning: {results_dir} does not exist")
            continue
        
        files = glob.glob(os.path.join(results_dir, "*_density*.csv"))
        
        for f in files:
            df = pd.read_csv(f, index_col=0)
            
            # Determine scheduler type
            if "Scheduler Type" in df.index:
                sched_type = str(df.loc["Scheduler Type", "Value"]).lower()
            elif os.path.basename(f).startswith("static_"):
                sched_type = "static"
            elif os.path.basename(f).startswith("dynamic_acab_"):
                sched_type = "dynamic_acab"
            elif os.path.basename(f).startswith("dynamic_adab_"):
                sched_type = "dynamic_adab"
            elif os.path.basename(f).startswith("dynamic_miad_"):
                sched_type = "dynamic_miad"
            else:
                continue
            
            # Extract B-PDR data
            if "Density" in df.index and ("Delivery Ratio" in df.index or "B-PDR" in df.index):
                density = float(df.loc["Density", "Value"])
                pdr = float(df.loc["B-PDR", "Value"]) if "B-PDR" in df.index else float(df.loc["Delivery Ratio", "Value"])
                pdr_std = float(df.loc["B-PDR", "StdDev"]) if "B-PDR" in df.index else float(df.loc["Delivery Ratio", "StdDev"])
                
                all_data.append({
                    'Density': density,
                    'B-PDR': pdr,
                    'StdDev': pdr_std,
                    'Scheduler': sched_type,
                    'MultihopMode': mode_name
                })
            
            # Extract collision rate data
            if "Density" in df.index and "Collision Rate" in df.index:
                density = float(df.loc["Density", "Value"])
                collision_rate = float(df.loc["Collision Rate", "Value"])
                collision_std = float(df.loc["Collision Rate", "StdDev"])
                
                all_data.append({
                    'Density': density,
                    'CollisionRate': collision_rate,
                    'CollisionStdDev': collision_std,
                    'Scheduler': sched_type,
                    'MultihopMode': mode_name
                })
            
            # Extract unique nodes data
            if "Density" in df.index and "Avg Unique Nodes Discovered" in df.index:
                density = float(df.loc["Density", "Value"])
                avg_unique = float(df.loc["Avg Unique Nodes Discovered", "Value"])
                avg_unique_std = float(df.loc["Avg Unique Nodes Discovered", "StdDev"])
                
                # Calculate percentage
                percentage = (avg_unique / (density - 1)) * 100 if density > 1 else 0
                percentage_std = (avg_unique_std / (density - 1)) * 100 if density > 1 else 0
                
                all_data.append({
                    'Density': density,
                    'AvgUniqueNodes': avg_unique,
                    'UniqueNodesStdDev': avg_unique_std,
                    'PercentageDiscovered': percentage,
                    'PercentageStdDev': percentage_std,
                    'Scheduler': sched_type,
                    'MultihopMode': mode_name
                })
    
    return pd.DataFrame(all_data)

def plot_bpdr_by_mode_comparison(base_dirs, output_dir, interval, interval_suffix):
    """
    Plot B-PDR comparison for each scheduler, comparing the three modes
    """
    df = get_density_dataframes_by_mode(base_dirs, interval_suffix)
    
    if df.empty or 'B-PDR' not in df.columns:
        print("  ⚠ No B-PDR data found")
        return
    
    # Filter to only B-PDR data
    pdr_df = df[df['B-PDR'].notna()].copy()
    
    densities = sorted(pdr_df["Density"].unique())
    schedulers = ["static", "dynamic_acab", "dynamic_adab", "dynamic_miad"]
    scheduler_labels = {"static": "SBP", "dynamic_adab": "ADAB", "dynamic_acab": "ACAB", "dynamic_miad": "MIAD"}
    modes = ["none", "append", "forward"]
    mode_labels = {"none": "Single-Hop", "append": "Append", "forward": "Forward"}
    mode_colors = {"none": "tab:blue", "append": "tab:orange", "forward": "tab:green"}
    
    # Create a 1x3 subplot for each scheduler
    fig, axes = plt.subplots(1, 3, figsize=(18, 6))
    
    bar_width = 0.25
    x = np.arange(len(densities))
    
    for ax, sched in zip(axes, schedulers):
        offset = -(len(modes) - 1) * bar_width / 2
        
        for i, mode in enumerate(modes):
            values = []
            errors = []
            
            for d in densities:
                rows = pdr_df[(pdr_df["Density"] == d) & 
                             (pdr_df["Scheduler"] == sched) & 
                             (pdr_df["MultihopMode"] == mode)]
                
                if not rows.empty:
                    values.append(rows["B-PDR"].mean())
                    errors.append(rows["StdDev"].mean())
                else:
                    values.append(0)
                    errors.append(0)
            
            ax.bar(x + offset + i * bar_width, values, bar_width, 
                   label=mode_labels[mode], color=mode_colors[mode])
            ax.errorbar(x + offset + i * bar_width, values, yerr=errors, fmt='none', 
                       ecolor='black', capsize=3, alpha=0.7)
        
        ax.set_xlabel("Total Buoys", fontsize=11)
        ax.set_ylabel("B-PDR", fontsize=11)
        ax.set_title(f"{scheduler_labels[sched]}", fontsize=12, fontweight='bold')
        ax.set_xticks(x)
        ax.set_xticklabels([str(int(d)) for d in densities])
        ax.legend(loc='lower right', fontsize=10)
        ax.grid(axis="y", linestyle="--", alpha=0.6)
    
    title = "B-PDR Comparison: Multihop Modes by Protocol"
    if interval:
        title += f" (Static Interval: {interval}s)"
    fig.suptitle(title, fontsize=14, fontweight='bold')
    
    plt.tight_layout()
    
    filename = f"mode_comparison_bpdr_interval{interval_suffix.replace('interval', '').replace('_ideal', '')}.png"
    plt.savefig(os.path.join(output_dir, filename), dpi=300)
    plt.close()
    print(f"  ✓ Saved {filename}")

def plot_collision_by_mode_comparison(base_dirs, output_dir, interval, interval_suffix):
    """
    Plot collision rate comparison for each scheduler, comparing the three modes
    """
    df = get_density_dataframes_by_mode(base_dirs, interval_suffix)
    
    if df.empty or 'CollisionRate' not in df.columns:
        print("  ⚠ No collision rate data found")
        return
    
    # Filter to only collision data
    coll_df = df[df['CollisionRate'].notna()].copy()
    
    densities = sorted(coll_df["Density"].unique())
    schedulers = ["static", "dynamic_acab", "dynamic_adab", "dynamic_miad"]
    scheduler_labels = {"static": "SBP", "dynamic_adab": "ADAB", "dynamic_acab": "ACAB",    "dynamic_miad": "MIAD"}
    modes = ["none", "append", "forward"]
    mode_labels = {"none": "Single-Hop", "append": "Append", "forward": "Forward"}
    mode_colors = {"none": "tab:blue", "append": "tab:orange", "forward": "tab:green"}
    
    # Create a 1x3 subplot for each scheduler
    fig, axes = plt.subplots(1, 3, figsize=(18, 6))
    
    bar_width = 0.25
    x = np.arange(len(densities))
    
    for ax, sched in zip(axes, schedulers):
        offset = -(len(modes) - 1) * bar_width / 2
        
        for i, mode in enumerate(modes):
            values = []
            errors = []
            
            for d in densities:
                rows = coll_df[(coll_df["Density"] == d) & 
                              (coll_df["Scheduler"] == sched) & 
                              (coll_df["MultihopMode"] == mode)]
                
                if not rows.empty:
                    values.append(rows["CollisionRate"].mean())
                    errors.append(rows["CollisionStdDev"].mean())
                else:
                    values.append(0)
                    errors.append(0)
            
            ax.bar(x + offset + i * bar_width, values, bar_width, 
                   label=mode_labels[mode], color=mode_colors[mode])
            ax.errorbar(x + offset + i * bar_width, values, yerr=errors, fmt='none', 
                       ecolor='black', capsize=3, alpha=0.7)
        
        ax.set_xlabel("Total Buoys", fontsize=11)
        ax.set_ylabel("Collision Rate", fontsize=11)
        ax.set_title(f"{scheduler_labels[sched]}", fontsize=12, fontweight='bold')
        ax.set_xticks(x)
        ax.set_xticklabels([str(int(d)) for d in densities])
        ax.legend(loc='upper left', fontsize=10)
        ax.grid(axis="y", linestyle="--", alpha=0.6)
    
    title = "Collision Rate Comparison: Multihop Modes by Protocol"
    if interval:
        title += f" (Static Interval: {interval}s)"
    fig.suptitle(title, fontsize=14, fontweight='bold')
    
    plt.tight_layout()
    
    filename = f"mode_comparison_collision_interval{interval_suffix.replace('interval', '').replace('_ideal', '')}.png"
    plt.savefig(os.path.join(output_dir, filename), dpi=300)
    plt.close()
    print(f"  ✓ Saved {filename}")

def plot_unique_nodes_by_mode_comparison(base_dirs, output_dir, interval, interval_suffix):
    """
    Plot unique nodes (as percentage) comparison for each scheduler, comparing the three modes
    """
    df = get_density_dataframes_by_mode(base_dirs, interval_suffix)
    
    if df.empty or 'PercentageDiscovered' not in df.columns:
        print("  ⚠ No unique nodes data found")
        return
    
    # Filter to only unique nodes data
    unique_df = df[df['PercentageDiscovered'].notna()].copy()
    
    densities = sorted(unique_df["Density"].unique())
    schedulers = ["static", "dynamic_acab", "dynamic_adab", "dynamic_miad"]
    scheduler_labels = {"static": "SBP", "dynamic_adab": "ADAB", "dynamic_acab": "ACAB",    "dynamic_miad": "MIAD"}
    modes = ["none", "append", "forward"]
    mode_labels = {"none": "Single-Hop", "append": "Append", "forward": "Forward"}
    mode_colors = {"none": "tab:blue", "append": "tab:orange", "forward": "tab:green"}
    
    # Create a 1x3 subplot for each scheduler
    fig, axes = plt.subplots(1, 3, figsize=(18, 6))
    
    bar_width = 0.25
    x = np.arange(len(densities))
    
    for ax, sched in zip(axes, schedulers):
        offset = -(len(modes) - 1) * bar_width / 2
        
        for i, mode in enumerate(modes):
            values = []
            errors = []
            
            for d in densities:
                rows = unique_df[(unique_df["Density"] == d) & 
                                (unique_df["Scheduler"] == sched) & 
                                (unique_df["MultihopMode"] == mode)]
                
                if not rows.empty:
                    values.append(rows["PercentageDiscovered"].mean())
                    errors.append(rows["PercentageStdDev"].mean())
                else:
                    values.append(0)
                    errors.append(0)
            
            ax.bar(x + offset + i * bar_width, values, bar_width, 
                   label=mode_labels[mode], color=mode_colors[mode])
            ax.errorbar(x + offset + i * bar_width, values, yerr=errors, fmt='none', 
                       ecolor='black', capsize=3, alpha=0.7)
        
        ax.set_xlabel("Total Buoys", fontsize=11)
        ax.set_ylabel("% of Network Discovered", fontsize=11)
        ax.set_ylim(0, 100)
        ax.set_title(f"{scheduler_labels[sched]}", fontsize=12, fontweight='bold')
        ax.set_xticks(x)
        ax.set_xticklabels([str(int(d)) for d in densities])
        ax.legend(loc='upper left', fontsize=10)
        ax.grid(axis="y", linestyle="--", alpha=0.6)
    
    title = "Network Discovery Comparison: Multihop Modes by Protocol"
    if interval:
        title += f" (Static Interval: {interval}s)"
    fig.suptitle(title, fontsize=14, fontweight='bold')
    
    plt.tight_layout()
    
    filename = f"mode_comparison_unique_nodes_interval{interval_suffix.replace('interval', '').replace('_ideal', '')}.png"
    plt.savefig(os.path.join(output_dir, filename), dpi=300)
    plt.close()
    print(f"  ✓ Saved {filename}")

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Compare multihop modes for each protocol")
    parser.add_argument("--none-dir", required=True, help="Directory with 'none' mode results")
    parser.add_argument("--append-dir", required=True, help="Directory with 'append' mode results")
    parser.add_argument("--forward-dir", required=True, help="Directory with 'forward' mode results")
    parser.add_argument("--output-dir", required=True, help="Output directory for comparison plots")
    
    args = parser.parse_args()
    
    base_dirs = {
        'none': args.none_dir,
        'append': args.append_dir,
        'forward': args.forward_dir
    }
    
    os.makedirs(args.output_dir, exist_ok=True)
    
    print(f"\n{'='*60}")
    print(f"Generating Mode Comparison Plots")
    print(f"{'='*60}")
    print(f"None mode dir:    {args.none_dir}")
    print(f"Append mode dir:  {args.append_dir}")
    print(f"Forward mode dir: {args.forward_dir}")
    print(f"Output dir:       {args.output_dir}")
    print(f"{'='*60}\n")
    
    # Find common intervals across all mode directories
    print("Detecting available intervals...")
    common_intervals = find_common_intervals(base_dirs)
    
    if not common_intervals:
        print(f"\n❌ Error: No common intervals found across all mode directories")
        print(f"\nAvailable intervals per mode:")
        for mode_name, base_dir in base_dirs.items():
            if os.path.exists(base_dir):
                subdirs = [d.replace('results_', '') for d in os.listdir(base_dir) if d.startswith("results_")]
                intervals = [extract_interval_from_dirname(s) for s in subdirs]
                intervals = [i for i in intervals if i is not None]
                print(f"  {mode_name}: {sorted(set(intervals))}")
        return
    
    print(f"✓ Found {len(common_intervals)} common interval(s): {sorted(common_intervals.keys())}\n")
    
    # Generate comparison plots for each interval
    for interval_value in sorted(common_intervals.keys()):
        interval_suffix = common_intervals[interval_value]
        
        print(f"Processing interval {interval_value}s ({interval_suffix})...")
        
        plot_bpdr_by_mode_comparison(base_dirs, args.output_dir, interval_value, interval_suffix)
        plot_collision_by_mode_comparison(base_dirs, args.output_dir, interval_value, interval_suffix)
        plot_unique_nodes_by_mode_comparison(base_dirs, args.output_dir, interval_value, interval_suffix)
        
        print()
    
    print(f"{'='*60}")
    print(f"✓ All comparison plots saved to: {args.output_dir}")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()