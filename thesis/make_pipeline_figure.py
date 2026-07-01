"""
Generate the buoy transmission-pipeline flowchart used in the thesis.
Run from the repository root:

    .venv/Scripts/python.exe thesis/make_pipeline_figure.py

Writes thesis/figures/tx_pipeline.pdf (vector) and .png. The diagram is a
single-column flow of the CSMA/CA transmission pipeline implemented in
src/buoys/buoy.py: the scheduler trigger, carrier sensing, DIFS, backoff, the
own-vs-forward priority at transmission, and the re-contention loop that drains
the forward queue one frame per contention win. Colours match the house style
of the hand-drawn SVGs (blue = event/handler, gray = decision, coral = action).

Layout note: every feedback edge is routed on a dedicated right-hand "bus"
lane (or a short left lane) that never crosses a box or another lane, so the
figure has no overlapping elements.
"""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

OUT = "thesis/figures"
os.makedirs(OUT, exist_ok=True)

# ---- house-style palette (from 01_trigger_pipeline.svg / 02_csma_pipeline.svg)
BLUE = dict(fc="#E6F1FB", ec="#185FA5", tc="#0C447C")   # eventi / handler
GRAY = dict(fc="#F1EFE8", ec="#5F5E5A", tc="#444441")   # decisioni
CORAL = dict(fc="#FAECE7", ec="#993C1D", tc="#712B13")  # azioni di invio
LINE = "#3d3d3a"
SUB = "#555555"

PITCH = 1.55          # vertical distance between consecutive rows
H = 0.82              # box height
W = 3.5               # default box width (fits the longest single-line title)
BUS_X = 4.4           # right-hand feedback lane (returns to CHANNEL_SENSE)
LEFT_X = -3.0         # short left lane (should_send? -> back to check)

boxes = {}            # name -> (cx, cy, w, h)


def row_y(i):
    return -i * PITCH


def add_box(name, cx, i, title, sub=None, style=BLUE, w=W, h=H):
    cy = row_y(i)
    boxes[name] = (cx, cy, w, h)
    p = FancyBboxPatch(
        (cx - w / 2, cy - h / 2), w, h,
        boxstyle="round,pad=0.02,rounding_size=0.12",
        linewidth=1.0, facecolor=style["fc"], edgecolor=style["ec"], zorder=2,
    )
    ax.add_patch(p)
    if sub:
        ax.text(cx, cy + 0.14, title, ha="center", va="center",
                fontsize=10.5, fontweight="bold", color=style["tc"], zorder=3)
        ax.text(cx, cy - 0.17, sub, ha="center", va="center",
                fontsize=8.5, color=SUB, zorder=3)
    else:
        ax.text(cx, cy, title, ha="center", va="center",
                fontsize=10.5, fontweight="bold", color=style["tc"], zorder=3)


def edge(name, side):
    cx, cy, w, h = boxes[name]
    return {
        "top": (cx, cy + h / 2),
        "bottom": (cx, cy - h / 2),
        "left": (cx - w / 2, cy),
        "right": (cx + w / 2, cy),
    }[side]


def arrow(p0, p1, head=True):
    style = "-|>" if head else "-"
    a = FancyArrowPatch(p0, p1, arrowstyle=style, mutation_scale=13,
                        shrinkA=0, shrinkB=0, linewidth=1.0,
                        color=LINE, zorder=1)
    ax.add_patch(a)


def label(x, y, text, fs=9, color=SUB, rot=0):
    ax.text(x, y, text, ha="center", va="center", fontsize=fs, color=color,
            rotation=rot,
            bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none"),
            zorder=4)


fig, ax = plt.subplots(figsize=(4.9, 11.2))

# --------------------------------------------------------------------- nodes
add_box("sched", 0, 0, "SCHEDULER_CHECK", "verifica periodica", BLUE)
add_box("should", 0, 1, "should_send() ?", "densita, mobilita", GRAY)
add_box("arm", 0, 2, "want_to_send = true", "avvia la pipeline", BLUE)
add_box("sense", 0, 3, "CHANNEL_SENSE", "rileva il canale", BLUE)
add_box("d1", 0, 4, "canale libero ?", "channel_is_busy()", GRAY)
add_box("difs", 0, 5, "attesa DIFS", "WAITING_DIFS", BLUE)
add_box("d2", 0, 6, "canale libero ?", "DIFS_COMPLETION", GRAY)
add_box("backoff", 0, 7, "backoff casuale", "b slot in [0, cw-1]", BLUE)
add_box("d3", 0, 8, "canale libero ?", "BACKOFF_COMPLETION", GRAY)
add_box("tx", 0, 9, "TRANSMISSION_START", None, CORAL)
add_box("dtx", 0, 10, "beacon proprio ?", "want_to_send", GRAY)
add_box("txown", -1.9, 11, "trasmetti", "beacon proprio", CORAL, w=2.9)
add_box("txfwd", 1.9, 11, "inoltra", "hop_limit - 1", CORAL, w=2.9)
add_box("dpend", 0, 12, "altri inoltri ?", "coda di forward", GRAY)
add_box("done", 0, 13, "processing = false", "torna a RECEIVING", BLUE)

# --------------------------------------------------------- main downward flow
arrow(edge("sched", "bottom"), edge("should", "top"))
arrow(edge("should", "bottom"), edge("arm", "top"))
label(0.34, (row_y(1) + row_y(2)) / 2, "si", color=SUB)
arrow(edge("arm", "bottom"), edge("sense", "top"))
arrow(edge("sense", "bottom"), edge("d1", "top"))
arrow(edge("d1", "bottom"), edge("difs", "top"))
label(0.32, (row_y(4) + row_y(5)) / 2, "libero")
arrow(edge("difs", "bottom"), edge("d2", "top"))
arrow(edge("d2", "bottom"), edge("backoff", "top"))
label(0.32, (row_y(6) + row_y(7)) / 2, "libero")
arrow(edge("backoff", "bottom"), edge("d3", "top"))
arrow(edge("d3", "bottom"), edge("tx", "top"))
label(0.32, (row_y(8) + row_y(9)) / 2, "libero")
arrow(edge("tx", "bottom"), edge("dtx", "top"))

# own vs forward split and merge
arrow(edge("dtx", "bottom"), edge("txown", "top"))
arrow(edge("dtx", "bottom"), edge("txfwd", "top"))
label(-1.15, row_y(10) - 0.62, "si")
label(1.15, row_y(10) - 0.62, "no")
arrow(edge("txown", "bottom"), edge("dpend", "top"))
arrow(edge("txfwd", "bottom"), edge("dpend", "top"))
arrow(edge("dpend", "bottom"), edge("done", "top"))
label(0.32, (row_y(12) + row_y(13)) / 2, "no")

# ------------------------------------------------ left lane: should_send = no
lx, ly0 = edge("should", "left")
sx, sy0 = edge("sched", "left")
arrow((lx, ly0), (LEFT_X, ly0), head=False)
arrow((LEFT_X, ly0), (LEFT_X, sy0), head=False)
arrow((LEFT_X, sy0), (sx, sy0))
label((lx + LEFT_X) / 2, ly0 - 0.24, "no")

# -------------------------------- right feedback bus: busy / more pending ->
# returns from d1, d2, d3 (canale occupato) and dpend (altri inoltri) all
# re-enter CHANNEL_SENSE. One vertical lane, joined by horizontal stubs.
bus_top = edge("sense", "right")[1]
bus_bottom = edge("dpend", "right")[1]
# vertical lane + single arrowhead back into CHANNEL_SENSE
arrow((BUS_X, bus_bottom), (BUS_X, bus_top), head=False)
arrow((BUS_X, bus_top), (edge("sense", "right")[0], bus_top))
label(BUS_X, (bus_top + bus_bottom) / 2, "torna a\nCHANNEL_SENSE",
      fs=8.5, rot=90)

for nm, txt in (("d1", "occupato"), ("d2", "occupato"),
                ("d3", "occupato"), ("dpend", "si")):
    ex, ey = edge(nm, "right")
    arrow((ex, ey), (BUS_X, ey), head=False)
    label((ex + BUS_X) / 2, ey + 0.22, txt)

# --------------------------------------------------------------------- frame
ax.set_xlim(-3.7, 5.3)
ax.set_ylim(row_y(13) - 0.9, 0.9)
ax.set_aspect("equal")
ax.axis("off")
fig.tight_layout(pad=0.3)
fig.savefig(f"{OUT}/tx_pipeline.pdf")
fig.savefig(f"{OUT}/tx_pipeline.png", dpi=150)
plt.close(fig)
print("wrote", f"{OUT}/tx_pipeline.pdf", "and .png")
