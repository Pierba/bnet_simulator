import argparse
import json
import os
import random

import matplotlib.pyplot as plt

from config.config_handler import ConfigHandler

# Colors used to tell the two buoy populations apart in the scatter plots.
MOBILE_COLOR = "tab:orange"
FIXED_COLOR = "tab:blue"

# Arrange buoys randomly within the world boundaries, ensuring they are not too
# close to the edges. Kept in sync with run.arrange_buoys_randomly so the plotted
# positions follow the same distribution the batch runner uses.
def arrange_buoys_randomly(n_buoys: int, world_width: float, world_height: float) -> list[tuple[float, float]]:
    positions: list[tuple[float, float]] = []
    for _ in range(n_buoys):
        x = random.uniform(10, world_width - 10)
        y = random.uniform(10, world_height - 10)
        positions.append((x, y))
    return positions

# Split a flat position list into (mobile, fixed) just like init.build_and_run does:
# the first mobile_count buoys are mobile, the remainder are fixed.
def split_mobile_fixed(positions: list[tuple[float, float]], mobile_percentage: float):
    total = len(positions)
    mobile_count = min(total, max(1, int(total * mobile_percentage))) if mobile_percentage > 0 else 0
    return positions[:mobile_count], positions[mobile_count:]

# Plot one scatter per density into output_dir, reusing positions already built by
# the caller (e.g. run.py passes the exact layout it just generated). Returns the
# directory the plots were written to.
def plot_initial_positions(
    positions_by_density: dict[int, list[tuple[float, float]]],
    mobile_percentage: float,
    world_width: float,
    world_height: float,
    output_dir: str,
) -> str:
    os.makedirs(output_dir, exist_ok=True)
    for density, positions in sorted(positions_by_density.items()):
        plot_path = os.path.join(output_dir, f"initial_positions_density{density}.png")
        plot_density(density, positions, mobile_percentage, world_width, world_height, plot_path)
    return output_dir

# Render a single density's initial layout as a dot per buoy in (x, y) space.
def plot_density(
    density: int,
    positions: list[tuple[float, float]],
    mobile_percentage: float,
    world_width: float,
    world_height: float,
    plot_path: str,
):
    mobile, fixed = split_mobile_fixed(positions, mobile_percentage)

    fig, ax = plt.subplots(figsize=(8, 8))

    if fixed:
        ax.scatter([p[0] for p in fixed], [p[1] for p in fixed],
                   s=40, color=FIXED_COLOR, edgecolors="black", linewidths=0.4,
                   label=f"Fixed ({len(fixed)})")
    if mobile:
        ax.scatter([p[0] for p in mobile], [p[1] for p in mobile],
                   s=40, color=MOBILE_COLOR, edgecolors="black", linewidths=0.4,
                   label=f"Mobile ({len(mobile)})")

    ax.set_xlim(0, world_width)
    ax.set_ylim(0, world_height)
    ax.set_aspect("equal")
    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    ax.set_title(f"Initial Buoy Positions — {density} Buoys")
    if mobile or fixed:
        ax.legend(loc="upper right")
    ax.grid(True, linestyle="--", alpha=0.5)

    plt.tight_layout()
    plt.savefig(plot_path)
    plt.close()

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot the initial buoy positions (one scatter per density) under metrics/")
    parser.add_argument(
        "--output-dir",
        type=str,
        default=os.path.join("metrics", "initial_positions"),
        help="Directory to create and write the per-density position plots into "
             "(default: metrics/initial_positions)",
    )
    parser.add_argument(
        "--positions-file",
        type=str,
        default=None,
        help="Path to a JSON file mapping density -> list of [x, y] positions. When "
             "provided these exact positions are plotted (run.py passes the layout it "
             "generated); otherwise a fresh layout is generated from the config sweep.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducible buoy layouts when generating positions "
             "(ignored if --positions-file is given; default: random each run)",
    )
    return parser.parse_args()

def main():
    args = parse_args()

    cfg = ConfigHandler()

    # World extent and mobile split mirror run.py so the plots match real batches
    world_width: float = cfg.get("world", "width")
    world_height: float = cfg.get("world", "height")
    mobile_percentage: float = cfg.get("buoys", "mobile_percentage")

    # Use the exact positions handed over by run.py, otherwise generate a fresh
    # layout from the config density sweep (standalone use).
    if args.positions_file:
        with open(args.positions_file, "r") as f:
            raw = json.load(f)
        # JSON object keys are strings; restore the integer densities
        positions_by_density = {int(density): positions for density, positions in raw.items()}
    else:
        if args.seed is not None:
            random.seed(args.seed)
        min_buoys: int = cfg.get("simulation", "min_buoys")
        max_buoys: int = cfg.get("simulation", "max_buoys")
        step_buoys: int = cfg.get("simulation", "step_buoys")
        densities = list(range(min_buoys, max_buoys + 1, step_buoys))
        positions_by_density = {
            density: arrange_buoys_randomly(density, world_width, world_height)
            for density in densities
        }

    print(f"Writing initial-position plots to: {args.output_dir}")
    plot_initial_positions(positions_by_density, mobile_percentage, world_width, world_height, args.output_dir)
    for density in sorted(positions_by_density):
        print(f"  density {density}: {len(positions_by_density[density])} buoys")

    print("Done.")

if __name__ == "__main__":
    main()
