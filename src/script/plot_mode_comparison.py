import os
import glob
import argparse
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# Schedulers plotted by default when --schedulers is not provided, and their display labels
DEFAULT_SCHEDULERS = ["static", "dynamic_acab", "dynamic_adab"]
SCHEDULER_LABELS = {"static": "SBP", "dynamic_acab": "ACAB", "dynamic_adab": "ADAB"}

# Multihop mode order, labels and colors used across every comparison figure
MODE_ORDER = ["none", "append", "forwarded"]
MODE_LABELS = {"none": "Single-Hop", "append": "Append", "forwarded": "Forward"}
MODE_COLORS = {"none": "tab:blue", "append": "tab:orange", "forwarded": "tab:green"}


def scheduler_from(df, filename):
    """Resolve the scheduler type from the CSV content, falling back to the filename."""
    if "Scheduler Type" in df.index:
        return str(df.loc["Scheduler Type", "Value"]).lower()
    base = os.path.basename(filename)
    if base.startswith("static_"):
        return "static"
    if base.startswith("dynamic_acab_"):
        return "dynamic_acab"
    if base.startswith("dynamic_adab_"):
        return "dynamic_adab"
    return "unknown"


def load_mode_data(mode_dirs):
    """Load every density summary CSV across all modes into a single long DataFrame.

    Returns columns: Density, Scheduler, Mode, PDR, CollisionRate, PercentageDiscovered.
    """
    rows = []
    for mode, results_dir in mode_dirs.items():
        if not os.path.isdir(results_dir):
            print(f"  [!]Results dir for '{mode}' not found: {results_dir}")
            continue

        for f in glob.glob(os.path.join(results_dir, "*.csv")):
            # Skip ramp time-series files; only density summaries are comparable here
            if f.endswith("_timeseries.csv"):
                continue

            df = pd.read_csv(f, index_col=0)
            if "Density" not in df.index:
                continue

            density = float(df.loc["Density", "Value"])
            scheduler = scheduler_from(df, f)

            pdr = float(df.loc["PDR", "Value"]) if "PDR" in df.index else np.nan
            collision = float(df.loc["Collision Rate", "Value"]) if "Collision Rate" in df.index else np.nan

            # Prefer the directly-exported percentage; otherwise derive it
            if "Avg % Network Discovered" in df.index:
                pct = float(df.loc["Avg % Network Discovered", "Value"])
            elif "Avg Unique Nodes Discovered" in df.index and density > 1:
                pct = float(df.loc["Avg Unique Nodes Discovered", "Value"]) / (density - 1) * 100
            else:
                pct = np.nan

            rows.append({
                "Density": density,
                "Scheduler": scheduler,
                "Mode": mode,
                "PDR": pdr,
                "CollisionRate": collision,
                "PercentageDiscovered": pct,
            })

    return pd.DataFrame(rows)


def plot_metric(df, value_col, ylabel, title, output_path, interval, schedulers, ylim=None, legend_loc="best"):
    """Render one figure: a subplot per scheduler with mode-grouped bars over density."""
    modes_present = [m for m in MODE_ORDER if m in df["Mode"].unique()]
    if not modes_present:
        print(f"  [!]No modes available for {value_col}")
        return

    metric_df = df[df[value_col].notna()]
    if metric_df.empty:
        print(f"  [!]No {value_col} data found")
        return

    # Only keep schedulers that actually have data for this metric so absent
    # protocols don't render as empty subplots.
    schedulers_present = [s for s in schedulers if s in set(metric_df["Scheduler"].unique())]
    if not schedulers_present:
        print(f"  [!]No scheduler data found for {value_col}")
        return

    densities = sorted(metric_df["Density"].unique())
    x = np.arange(len(densities))
    bar_width = 0.8 / len(modes_present)

    fig, axes = plt.subplots(1, len(schedulers_present), figsize=(6 * len(schedulers_present), 6), squeeze=False)
    axes = axes[0]

    for ax, sched in zip(axes, schedulers_present):
        offset = -(len(modes_present) - 1) * bar_width / 2
        for i, mode in enumerate(modes_present):
            values = []
            for d in densities:
                rows = metric_df[(metric_df["Density"] == d) &
                                 (metric_df["Scheduler"] == sched) &
                                 (metric_df["Mode"] == mode)]
                values.append(rows[value_col].mean() if not rows.empty else 0)
            ax.bar(x + offset + i * bar_width, values, bar_width,
                   label=MODE_LABELS.get(mode, mode), color=MODE_COLORS.get(mode))

        ax.set_xlabel("Total Buoys", fontsize=11)
        ax.set_ylabel(ylabel, fontsize=11)
        ax.set_title(SCHEDULER_LABELS.get(sched, sched), fontsize=12, fontweight="bold")
        ax.set_xticks(x)
        ax.set_xticklabels([str(int(d)) for d in densities])
        if ylim:
            ax.set_ylim(*ylim)
        ax.legend(loc=legend_loc, fontsize=10)
        ax.grid(axis="y", linestyle="--", alpha=0.6)

    suptitle = title
    if interval:
        suptitle += f" (Static Interval: {interval}s)"
    # wrap so the title still fits when only one scheduler subplot is drawn
    fig.suptitle(suptitle, fontsize=14, fontweight="bold", wrap=True)

    plt.tight_layout()
    plt.savefig(output_path, dpi=200)
    plt.close()
    print(f"  [OK]Saved {os.path.basename(output_path)}")


def generate_comparison_plots(mode_dirs, output_dir, interval=None, schedulers=None):
    """Render the PDR / collision / discovery cross-mode comparison figures.

    mode_dirs maps a multihop mode name to a results directory holding density
    summary CSVs (raw single-run or seed-averaged - both expose the same metric
    rows). Returns True when at least the data load succeeded.
    """
    schedulers = schedulers or DEFAULT_SCHEDULERS
    if not mode_dirs:
        print("No mode dirs provided; nothing to compare.")
        return False

    os.makedirs(output_dir, exist_ok=True)

    print(f"\n{'='*60}")
    print("Generating Multihop Mode Comparison Plots")
    print(f"{'='*60}")
    for mode, results_dir in mode_dirs.items():
        print(f"  {mode:<10} -> {results_dir}")
    print(f"  output     -> {output_dir}")
    print(f"{'='*60}\n")

    df = load_mode_data(mode_dirs)
    if df.empty:
        print("No comparable density data found across modes.")
        return False

    tag = str(interval).replace('.', '_') if interval else "NA"

    plot_metric(
        df, "PDR", "PDR",
        "PDR Comparison: Multihop Modes by Protocol",
        os.path.join(output_dir, f"mode_comparison_pdr_interval-{tag}.png"),
        interval, schedulers, legend_loc="lower right",
    )
    plot_metric(
        df, "CollisionRate", "Collision Rate",
        "Collision Rate Comparison: Multihop Modes by Protocol",
        os.path.join(output_dir, f"mode_comparison_collision_rate_interval-{tag}.png"),
        interval, schedulers, legend_loc="upper left",
    )
    plot_metric(
        df, "PercentageDiscovered", "Avg % of Network Discovered",
        "Network Discovery Comparison: Multihop Modes by Protocol",
        os.path.join(output_dir, f"mode_comparison_avg_percentage_network_discovered_interval-{tag}.png"),
        interval, schedulers, ylim=(0, 100), legend_loc="upper left",
    )

    print(f"\n{'='*60}")
    print(f"[OK]All comparison plots saved to: {output_dir}")
    print(f"{'='*60}\n")
    return True


def main():
    parser = argparse.ArgumentParser(description="Compare multihop modes for each protocol")
    parser.add_argument("--mode-dir", nargs=2, action="append", default=[],
                        metavar=("MODE", "DIR"),
                        help="A multihop mode name and its results directory (repeatable)")
    parser.add_argument("--output-dir", required=True, help="Output directory for comparison plots")
    parser.add_argument("--interval", type=float, default=None,
                        help="Static interval value to display in titles")
    parser.add_argument("--schedulers", nargs="+", default=None,
                        help=f"Schedulers to plot, one subplot each (default: {' '.join(DEFAULT_SCHEDULERS)})")
    args = parser.parse_args()

    mode_dirs = {mode: results_dir for mode, results_dir in args.mode_dir}
    generate_comparison_plots(mode_dirs, args.output_dir, args.interval, args.schedulers)


if __name__ == "__main__":
    main()
