"""
Rigenera i grafici del confronto (Sez. 4.4) dai CSV della cartella "comparazioni ultime",
con scale leggibili, ordine pannelli SBP-ADAB-ACAB ed etichette in italiano.
Uso: python thesis/make_cap4_figures.py  (adattare SRC al percorso della cartella dati)
Output: thesis/figures_cap4/{senza_errore,con_errore}/*.pdf|png
"""
import csv, os, glob, re
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

SRC = "../comparazioni ultime"
OUT = "thesis/figures_cap4"

SCHED = ["static", "dynamic_adab", "dynamic_acab"]
SCHED_TITLE = {"static": "SBP", "dynamic_adab": "ADAB", "dynamic_acab": "ACAB"}
MODES = ["none", "append", "forward"]
MODE_LABEL = {"none": "Single-hop", "append": "Forwarding aggregato", "forward": "Forwarding singolo"}
MODE_COLOR = {"none": "#1f77b4", "append": "#ff7f0e", "forward": "#2ca02c"}
DENS = [20, 40, 60, 80, 100]

plt.rcParams.update({
    "font.size": 13,
    "axes.titlesize": 15,
    "axes.labelsize": 13,
    "legend.fontsize": 11,
    "figure.dpi": 150,
    "savefig.bbox": "tight",
    "axes.grid": True,
    "grid.alpha": 0.35,
    "grid.linestyle": "--",
})

def chan_dir(chan):
    return os.path.join(SRC, "senza errore" if chan == "ideal" else "con errore")

def res_dir(chan, mode, interval="1.0"):
    tag = f"interval-{interval}_ideal_random" if chan == "ideal" else f"interval-{interval}_random"
    return os.path.join(chan_dir(chan), f"results_{tag}_{mode}")

def read_summary(path):
    val, std = {}, {}
    with open(path) as f:
        for row in csv.reader(f):
            if len(row) >= 2 and row[0]:
                try: val[row[0]] = float(row[1])
                except ValueError: val[row[0]] = row[1]
                try: std[row[0]] = float(row[2])
                except (ValueError, IndexError): std[row[0]] = 0.0
    return val, std

def series(chan, mode, sched, metric):
    ys, es = [], []
    for d in DENS:
        v, s = read_summary(os.path.join(res_dir(chan, mode), f"{sched}_random_density{d}.csv"))
        ys.append(v[metric]); es.append(s.get(metric, 0.0))
    return np.array(ys), np.array(es)

def bar_figure(chan, metric, ylabel, fname, ylim=None, pct=False):
    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.2), sharey=True)
    width = 0.26
    x = np.arange(len(DENS))
    ymax = 0
    for ax, sched in zip(axes, SCHED):
        for k, mode in enumerate(MODES):
            ys, es = series(chan, mode, sched, metric)
            ymax = max(ymax, (ys + es).max())
            ax.bar(x + (k - 1) * width, ys, width, yerr=es, capsize=2.5,
                   error_kw={"lw": 0.9, "alpha": 0.8},
                   label=MODE_LABEL[mode], color=MODE_COLOR[mode])
        ax.set_title(SCHED_TITLE[sched])
        ax.set_xticks(x); ax.set_xticklabels(DENS)
        ax.set_xlabel("Numero di boe")
        ax.set_axisbelow(True)
    axes[0].set_ylabel(ylabel)
    if ylim is None:
        ylim = (0, min(1.0, ymax * 1.12) if not pct else 100)
    axes[0].set_ylim(*ylim)
    axes[0].legend(loc="upper left" if metric in ("Collision Rate",) else "lower left",
                   framealpha=0.9)
    fig.tight_layout()
    sub = "senza_errore" if chan == "ideal" else "con_errore"
    os.makedirs(os.path.join(OUT, sub), exist_ok=True)
    for ext in ("pdf", "png"):
        fig.savefig(os.path.join(OUT, sub, f"{fname}.{ext}"))
    plt.close(fig)
    print("wrote", sub, fname)

def growth_figure(chan, fname):
    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.2), sharey=True)
    for ax, sched in zip(axes, SCHED):
        for mode in MODES:
            p = os.path.join(res_dir(chan, mode), f"{sched}_random_density100_discovery.csv")
            t, y, s = [], [], []
            with open(p) as f:
                rd = csv.reader(f); next(rd)
                for row in rd:
                    tt = float(row[0])
                    if tt > 198.5:  # taglia l'artefatto finale
                        continue
                    t.append(tt); y.append(float(row[1])); s.append(float(row[2]))
            t, y, s = map(np.array, (t, y, s))
            ax.plot(t, y, color=MODE_COLOR[mode], label=MODE_LABEL[mode], lw=1.6)
            ax.fill_between(t, np.clip(y - s, 0, 100), np.clip(y + s, 0, 100),
                            color=MODE_COLOR[mode], alpha=0.18, lw=0)
        ax.set_title(SCHED_TITLE[sched])
        ax.set_xlabel("Tempo (s)")
        ax.set_xlim(0, 200)
        ax.set_axisbelow(True)
    axes[0].set_ylabel("% media di rete scoperta")
    axes[0].set_ylim(0, 100)
    axes[0].legend(loc="lower right", framealpha=0.9)
    fig.tight_layout()
    sub = "senza_errore" if chan == "ideal" else "con_errore"
    os.makedirs(os.path.join(OUT, sub), exist_ok=True)
    for ext in ("pdf", "png"):
        fig.savefig(os.path.join(OUT, sub, f"{fname}.{ext}"))
    plt.close(fig)
    print("wrote", sub, fname)

for chan in ("ideal", "error"):
    bar_figure(chan, "Avg % Network Discovered", "% media di rete scoperta",
               "mode_comparison_avg_percentage_network_discovered_interval-1_0",
               ylim=(0, 100))
    bar_figure(chan, "PDR", "B-PDR",
               "mode_comparison_pdr_interval-1_0", ylim=(0, 1.0))
    bar_figure(chan, "Collision Rate", "Collision rate",
               "mode_comparison_collision_rate_interval-1_0")
    growth_figure(chan, "mode_comparison_discovery_growth_interval-1_0")
bar_figure("error", "Loss Rate", "Packet loss",
           "mode_comparison_loss_rate_interval-1_0")
print("done")
