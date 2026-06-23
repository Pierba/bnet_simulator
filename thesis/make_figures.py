"""
Generate the thesis figures from the averaged metric CSVs in metrics/averaged/.
Run from the repository root:  .venv/Scripts/python.exe thesis/make_figures.py
All figures are written to thesis/figures/ as PDF (vector) + PNG.
"""
import csv
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

BASE = "metrics/averaged"
OUT = "thesis/figures"
os.makedirs(OUT, exist_ok=True)

SCHED = ["static", "dynamic_adab", "dynamic_acab"]
SCHED_LABEL = {"static": "SBP (static)", "dynamic_adab": "ADAB", "dynamic_acab": "ACAB"}
SCHED_COLOR = {"static": "#c0392b", "dynamic_adab": "#2980b9", "dynamic_acab": "#27ae60"}
SCHED_MARK = {"static": "o", "dynamic_adab": "s", "dynamic_acab": "^"}
MODE_STYLE = {"append": "-", "forwarded": "--"}
DENS = [20, 40, 60, 80, 100]

plt.rcParams.update({
    "font.size": 12,
    "axes.grid": True,
    "grid.alpha": 0.3,
    "figure.dpi": 130,
    "savefig.bbox": "tight",
})


def read_csv(path):
    val, std = {}, {}
    if not os.path.exists(path):
        return val, std
    with open(path) as f:
        for row in csv.reader(f):
            if len(row) >= 3 and row[0] not in ("", "Metric"):
                try:
                    val[row[0]] = float(row[1])
                except ValueError:
                    val[row[0]] = row[1]
                try:
                    std[row[0]] = float(row[2])
                except (ValueError, IndexError):
                    std[row[0]] = 0.0
    return val, std


def series(mode, interval, sched, metric):
    xs, ys, es = [], [], []
    for d in DENS:
        p = f"{BASE}/results_interval-{interval}_random_{mode}/{sched}_random_density{d}.csv"
        v, s = read_csv(p)
        if metric in v:
            xs.append(d)
            ys.append(v[metric])
            es.append(s.get(metric, 0.0))
    return xs, ys, es


def avg_neighbors(mode, interval, sched="static"):
    xs, ys, _ = series(mode, interval, sched, "Average Neighbors")
    return xs, ys


def save(fig, name):
    fig.savefig(f"{OUT}/{name}.pdf")
    fig.savefig(f"{OUT}/{name}.png")
    plt.close(fig)
    print("wrote", name)


# ---------- scheduler comparison (single mode, single interval) ----------
def scheduler_comparison(mode, interval, metric, ylabel, name,
                         logy=False, twin_neighbors=False, pct=False, errbar=True):
    fig, ax = plt.subplots(figsize=(6.4, 4.3))
    for sc in SCHED:
        xs, ys, es = series(mode, interval, sc, metric)
        if pct:
            ys = [y * 100 for y in ys]
            es = [e * 100 for e in es]
        if errbar:
            ax.errorbar(xs, ys, yerr=es, marker=SCHED_MARK[sc], color=SCHED_COLOR[sc],
                        capsize=3, lw=2, label=SCHED_LABEL[sc])
        else:
            ax.plot(xs, ys, marker=SCHED_MARK[sc], color=SCHED_COLOR[sc], lw=2,
                    label=SCHED_LABEL[sc])
    ax.set_xlabel("Total buoys (density)")
    ax.set_ylabel(ylabel)
    if logy:
        ax.set_yscale("log")
    ax.set_xticks(DENS)
    if twin_neighbors:
        axn = ax.twinx()
        xs, ys = avg_neighbors(mode, interval)
        axn.plot(xs, ys, color="0.4", lw=1.4, ls=":", marker="D", ms=4,
                 label="Avg. neighbours")
        axn.set_ylabel("Average neighbours")
        axn.grid(False)
    ax.legend(loc="best", fontsize=10)
    save(fig, name)


# ---------- mode comparison (append vs forwarded) ----------
def mode_comparison(interval, scheds, metric, ylabel, name, logy=False, pct=False):
    fig, ax = plt.subplots(figsize=(6.6, 4.3))
    for sc in scheds:
        for mode in ("append", "forwarded"):
            xs, ys, es = series(mode, interval, sc, metric)
            if pct:
                ys = [y * 100 for y in ys]
            lbl = f"{SCHED_LABEL[sc]} — {mode}"
            ax.plot(xs, ys, MODE_STYLE[mode], marker=SCHED_MARK[sc],
                    color=SCHED_COLOR[sc], lw=2, label=lbl,
                    alpha=1.0 if mode == "append" else 0.7)
    ax.set_xlabel("Total buoys (density)")
    ax.set_ylabel(ylabel)
    if logy:
        ax.set_yscale("log")
    ax.set_xticks(DENS)
    ax.legend(loc="best", fontsize=9)
    save(fig, name)


# ---------- interval sensitivity (single scheduler, single mode) ----------
def interval_sensitivity(mode, sched, metric, ylabel, name, pct=False, logy=False):
    fig, ax = plt.subplots(figsize=(6.4, 4.3))
    colors = {"0.25": "#8e44ad", "0.5": "#e67e22", "1.0": "#16a085"}
    for interval in ("0.25", "0.5", "1.0"):
        xs, ys, es = series(mode, interval, sched, metric)
        if pct:
            ys = [y * 100 for y in ys]
        ax.plot(xs, ys, marker="o", color=colors[interval], lw=2,
                label=f"min interval = {interval}s")
    ax.set_xlabel("Total buoys (density)")
    ax.set_ylabel(ylabel)
    if logy:
        ax.set_yscale("log")
    ax.set_xticks(DENS)
    ax.legend(loc="best", fontsize=10)
    save(fig, name)


# ---------- energy: normalized airtime bar ----------
def airtime_bars(interval, mode, name):
    import numpy as np
    fig, ax = plt.subplots(figsize=(6.6, 4.3))
    width = 0.25
    x = np.arange(len(DENS))
    for i, sc in enumerate(SCHED):
        _, ys, _ = series(mode, interval, sc, "Sent")
        ax.bar(x + (i - 1) * width, ys, width, color=SCHED_COLOR[sc],
               label=SCHED_LABEL[sc])
    ax.set_yscale("log")
    ax.set_xlabel("Total buoys (density)")
    ax.set_ylabel("Beacon transmissions (log scale)")
    ax.set_xticks(x)
    ax.set_xticklabels(DENS)
    ax.legend(fontsize=10)
    save(fig, name)


if __name__ == "__main__":
    I = "0.25"
    # Scheduler comparison under append (primary)
    scheduler_comparison("append", I, "PDR", "Per-receiver delivery ratio (PDR)",
                         "sched_pdr_append", twin_neighbors=True)
    scheduler_comparison("append", I, "Delivery Ratio",
                         "Network delivery ratio (unique beacons)",
                         "sched_deliv_append", twin_neighbors=True)
    scheduler_comparison("append", I, "Collision Rate", "Collision rate",
                         "sched_collision_append", twin_neighbors=True)
    scheduler_comparison("append", I, "Sent", "Beacon transmissions",
                         "sched_sent_append", logy=True)
    scheduler_comparison("append", I, "Avg % Network Discovered",
                         "Avg. network discovered (%)", "sched_netdisc_append")

    # Scheduler comparison under forwarded (mirror, collisions + PDR)
    scheduler_comparison("forwarded", I, "Collision Rate", "Collision rate",
                         "sched_collision_forwarded", twin_neighbors=True)
    scheduler_comparison("forwarded", I, "PDR", "Per-receiver delivery ratio (PDR)",
                         "sched_pdr_forwarded", twin_neighbors=True)

    # Mode comparison: append vs forwarded
    mode_comparison(I, ["static", "dynamic_adab"], "Collision Rate",
                    "Collision rate", "mode_collision")
    mode_comparison(I, ["static", "dynamic_adab"], "Sent",
                    "Beacon transmissions", "mode_sent", logy=True)
    mode_comparison(I, ["static", "dynamic_adab"], "Avg % Network Discovered",
                    "Avg. network discovered (%)", "mode_netdisc", pct=False)
    mode_comparison(I, ["static", "dynamic_adab"], "Avg Reaction Latency",
                    "Avg. reaction latency (s)", "mode_reaction")

    # Interval sensitivity (ADAB, append)
    interval_sensitivity("append", "dynamic_adab", "Collision Rate",
                         "Collision rate", "interval_collision_adab")
    interval_sensitivity("append", "dynamic_adab", "Sent",
                         "Beacon transmissions", "interval_sent_adab", logy=True)

    # Energy bars
    airtime_bars(I, "append", "energy_airtime_append")

    print("done")
