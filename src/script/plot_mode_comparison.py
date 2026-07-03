import os
import re
import glob
import argparse
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

# Schedulers plotted by default when --schedulers is not provided, and their display labels
DEFAULT_SCHEDULERS = ["static", "dynamic_acab", "dynamic_adab"]
SCHEDULER_LABELS = {"static": "SBP", "dynamic_acab": "ACAB", "dynamic_adab": "ADAB"}

# Multihop mode order, labels and colors used across every comparison figure
MODE_ORDER = ["none", "append", "forward"]
MODE_LABELS = {"none": "Single-Hop", "append": "Append", "forward": "Forward"}
MODE_COLORS = {"none": "tab:blue", "append": "tab:orange", "forward": "tab:green"}


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

    Returns columns: Density, Scheduler, Mode, PDR, CollisionRate, LossRate,
    PercentageDiscovered, NeighborReceiverRatio, NeighborReceiverDelta.
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
            loss = float(df.loc["Loss Rate", "Value"]) if "Loss Rate" in df.index else np.nan
            ratio = (float(df.loc["Avg Neighbors to Receivers Ratio", "Value"])
                     if "Avg Neighbors to Receivers Ratio" in df.index else np.nan)
            delta = (float(df.loc["Avg Neighbors to Receivers Delta", "Value"])
                     if "Avg Neighbors to Receivers Delta" in df.index else np.nan)

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
                "LossRate": loss,
                "PercentageDiscovered": pct,
                "NeighborReceiverRatio": ratio,
                "NeighborReceiverDelta": delta,
            })

    return pd.DataFrame(rows)


def plot_metric(df, value_col, ylabel, title, output_path, interval, schedulers, ylim=None, legend_loc="best", refline=None, delta_overlay=False):
    """Render one figure: a subplot per scheduler with mode-grouped bars over density.

    With delta_overlay=True each subplot also carries a twin axis with one
    neighbors-receivers delta line per mode (dashed, mode-colored), every point
    labelled with its signed value (+X.X / -X.X) - mirroring the delta overlay
    embedded in the per-dir ratio plots of plot_metrics.py and avg_metrics.py.
    """
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

    # Shared twin-axis limits (0 always in view) so the delta lines stay
    # visually comparable across the scheduler subplots
    delta_df = pd.DataFrame()
    if delta_overlay and "NeighborReceiverDelta" in df.columns:
        delta_df = df[df["NeighborReceiverDelta"].notna()]
        if not delta_df.empty:
            d_lower = min(0.0, delta_df["NeighborReceiverDelta"].min())
            d_upper = max(0.0, delta_df["NeighborReceiverDelta"].max())
            d_pad = 0.15 * max(d_upper - d_lower, 1.0)

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

        if refline is not None:
            ax.axhline(refline, color="gray", linestyle="--", linewidth=1)

        # Twin-axis delta overlay: one dashed line per mode, signed point labels
        delta_drawn = False
        if not delta_df.empty:
            ax2 = ax.twinx()
            ax2.set_ylabel("Avg Neighbors - Receivers in Range", fontsize=11)
            ax2.grid(False)

            for mode in modes_present:
                dvalues = []
                for d in densities:
                    rows = delta_df[(delta_df["Density"] == d) &
                                    (delta_df["Scheduler"] == sched) &
                                    (delta_df["Mode"] == mode)]
                    # NaN breaks the line instead of faking a zero for a missing density
                    dvalues.append(rows["NeighborReceiverDelta"].mean() if not rows.empty else np.nan)

                dvalues_arr = np.array(dvalues, dtype=float)
                if np.isnan(dvalues_arr).all():
                    continue

                delta_drawn = True
                color = MODE_COLORS.get(mode)
                ax2.plot(x, dvalues_arr, marker='o', linestyle='--', linewidth=1, color=color)

                for x_pos, value in zip(x, dvalues_arr):
                    if np.isnan(value):
                        continue
                    # Neutral ink on a white pad keeps labels readable over the bars;
                    # the mode-colored marker next to them already carries identity
                    ax2.text(x_pos, value, f"{value:+.1f}", color="#212121", ha='center',
                             va='bottom' if value >= 0 else 'top', fontsize=8,
                             bbox=dict(boxstyle="round,pad=0.15", facecolor="white",
                                       edgecolor="none", alpha=0.7))

            ax2.set_ylim(d_lower - d_pad, d_upper + d_pad)

        ax.set_xlabel("Total Buoys", fontsize=11)
        ax.set_ylabel(ylabel, fontsize=11)
        ax.set_title(SCHEDULER_LABELS.get(sched, sched), fontsize=12, fontweight="bold")
        ax.set_xticks(x)
        ax.set_xticklabels([str(int(d)) for d in densities])
        if ylim:
            ax.set_ylim(*ylim)

        handles, labels = ax.get_legend_handles_labels()
        if delta_drawn:
            # Proxy entry: the actual lines are mode-colored, dashed marks them as deltas
            handles.append(Line2D([0], [0], color='black', marker='o', linestyle='--', linewidth=1))
            labels.append("Avg Delta (per mode)")
        ax.legend(handles, labels, loc=legend_loc, fontsize=10)
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


def densest_discovery_file(results_dir, scheduler):
    """Return (density, path) of the densest discovery series for a scheduler, or None.

    Each scheduler drops one ``{scheduler}_..._density{D}_discovery.csv`` per density;
    we pick the largest density (the last value of the sweep), matching the per-mode
    discovery plot in plot_metrics.py.
    """
    best = None
    for f in glob.glob(os.path.join(results_dir, f"{scheduler}_*_discovery.csv")):
        m = re.search(r"density(\d+(?:\.\d+)?)_discovery\.csv$", os.path.basename(f))
        if not m:
            continue
        density = float(m.group(1))
        if best is None or density > best[0]:
            best = (density, f)
    return best


def plot_discovery_over_time(mode_dirs, output_path, interval, schedulers):
    """Render the cross-mode network-discovery growth comparison.

    One subplot per scheduler; within each, the densest run's discovery curve for
    every multihop mode is overlaid so the modes can be compared over time. When the
    series were seed-averaged an ``avg_percentage_discovered_std`` band is drawn too.
    """
    modes_present = [m for m in MODE_ORDER if m in mode_dirs]
    if not modes_present:
        print("  [!]No modes available for discovery comparison")
        return

    # Keep only schedulers that have a discovery series in at least one mode
    schedulers_present = [
        s for s in schedulers
        if any(densest_discovery_file(mode_dirs[m], s) for m in modes_present)
    ]
    if not schedulers_present:
        print("  [!]No discovery time-series data found")
        return

    fig, axes = plt.subplots(1, len(schedulers_present), figsize=(7 * len(schedulers_present), 6), squeeze=False)
    axes = axes[0]

    max_density = None
    for ax, sched in zip(axes, schedulers_present):
        for mode in modes_present:
            found = densest_discovery_file(mode_dirs[mode], sched)
            if not found:
                continue
            density, path = found
            df = pd.read_csv(path)
            if "time" not in df.columns or "avg_percentage_discovered" not in df.columns or df.empty:
                continue

            max_density = density if max_density is None else max(max_density, density)
            color = MODE_COLORS.get(mode)
            ax.plot(df["time"], df["avg_percentage_discovered"],
                    label=MODE_LABELS.get(mode, mode), color=color)

            if "avg_percentage_discovered_std" in df.columns:
                lower = np.maximum(df["avg_percentage_discovered"] - df["avg_percentage_discovered_std"], 0)
                upper = np.minimum(df["avg_percentage_discovered"] + df["avg_percentage_discovered_std"], 100)
                ax.fill_between(df["time"], lower, upper, color=color, alpha=0.2)

        ax.set_xlabel("Time (s)", fontsize=11)
        ax.set_ylabel("Avg % of Network Discovered", fontsize=11)
        ax.set_ylim(0, 100)
        ax.set_title(SCHEDULER_LABELS.get(sched, sched), fontsize=12, fontweight="bold")
        ax.legend(loc="lower right", fontsize=10)
        ax.grid(True, linestyle="--", alpha=0.6)

    suptitle = "Network Discovery Growth Comparison: Multihop Modes by Protocol"
    if max_density is not None:
        suptitle += f" — Densest Run: {int(max_density)} Buoys"
    if interval:
        suptitle += f" (Static Interval: {interval}s)"
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
        interval, schedulers, ylim=(0, 1), legend_loc="lower right",
    )
    plot_metric(
        df, "CollisionRate", "Collision Rate",
        "Collision Rate Comparison: Multihop Modes by Protocol",
        os.path.join(output_dir, f"mode_comparison_collision_rate_interval-{tag}.png"),
        interval, schedulers, ylim=(0, 1), legend_loc="upper left",
    )
    plot_metric(
        df, "LossRate", "Loss Rate",
        "Loss Rate (Total Error) Comparison: Multihop Modes by Protocol",
        os.path.join(output_dir, f"mode_comparison_loss_rate_interval-{tag}.png"),
        interval, schedulers, ylim=(0, 1), legend_loc="upper left",
    )
    plot_metric(
        df, "PercentageDiscovered", "Avg % of Network Discovered",
        "Network Discovery Comparison: Multihop Modes by Protocol",
        os.path.join(output_dir, f"mode_comparison_avg_percentage_network_discovered_interval-{tag}.png"),
        interval, schedulers, ylim=(0, 100), legend_loc="upper left",
    )
    # Neighbors-to-receivers ratio is unbounded: 0-based axis with headroom above the
    # data, never below the parity refline at 1 so modes stay visually comparable.
    ratio_values = df["NeighborReceiverRatio"].dropna()
    ratio_ylim = (0, max(2.0, ratio_values.max() * 1.2)) if not ratio_values.empty else (0, 2.0)
    plot_metric(
        df, "NeighborReceiverRatio", "Avg Neighbors / Receivers in Range",
        "Neighbors-to-Receivers Ratio Comparison: Multihop Modes by Protocol",
        os.path.join(output_dir, f"mode_comparison_neighbor_receiver_ratio_interval-{tag}.png"),
        interval, schedulers, ylim=ratio_ylim, legend_loc="upper left", refline=1.0,
        delta_overlay=True,
    )
    plot_discovery_over_time(
        mode_dirs,
        os.path.join(output_dir, f"mode_comparison_discovery_growth_interval-{tag}.png"),
        interval, schedulers,
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
