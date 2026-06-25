# BNet Simulator

![Python](https://img.shields.io/badge/python-%E2%89%A53.11-blue)
![Managed with uv](https://img.shields.io/badge/managed%20with-uv-purple)
![Status](https://img.shields.io/badge/status-research%20prototype-orange)

A discrete-event simulator for testing and comparing **beacon-scheduling** and
**multihop** strategies in a simulated buoy network. It sweeps buoy densities and
beacon intervals, runs each combination per scheduler across one of several
mobility/topology scenarios, and produces metric CSVs and comparison plots.

The simulator is fully event-driven: every buoy emits beacons through a CSMA
contention pipeline, and a per-buoy scheduler decides *when* to transmit while the
multihop mode decides *what* each beacon carries and whether received beacons are
re-transmitted.

---

## Table of contents

- [Features](#features)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Batch runner flags](#batch-runner-flags)
- [Configuration](#configuration)
- [Output](#output)
- [Averaging multiple runs](#averaging-multiple-runs)
- [Project structure](#project-structure)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [Authors](#authors)
- [License](#license)

---

## Features

- **Three beacon schedulers** — fixed-interval and two density-aware adaptive
  backoff strategies, run side by side on identical topologies.
- **Three multihop modes** — single-hop, piggybacked topology, and TTL-bounded
  forwarding, each with its own tunable horizon.
- **Three scenarios** — static populations, gradual ramp-up, and churning
  random activation/deactivation.
- **Parameter sweeps** — buoy density and beacon interval are swept automatically;
  runs are distributed across worker processes.
- **Reproducible** — a single master seed derives every per-simulation seed, so a
  run can be repeated exactly.
- **Metrics & plots** — per-run CSVs plus per-mode and cross-mode comparison plots,
  with a helper to average many runs together.

## How it works

- **Schedulers** (`simulation.schedulers`): `static` (fixed interval),
  `dynamic_adab` (density-driven backoff), `dynamic_acab` (density +
  contact-recency + mobility blend).
- **Scenarios** (`simulation.scenario`): `static` (fixed buoy count), `ramp`
  (buoys activate one at a time), `random` (buoys randomly activate/deactivate
  over time).
- **Multihop modes** (`simulation.multihop_mode`): `none` (1-hop), `append`
  (piggyback learned topology onto own beacons), `forwarded` (re-broadcast
  others' beacons with a TTL).

The batch runner ([`src/run.py`](src/run.py)) builds the topology for each
density, then for every beacon interval launches one simulation per
`(scheduler × multihop mode × density)` combination as a parallel subprocess.
Each subprocess runs a single discrete-event simulation and writes its metrics to
CSV; the runner then drives the plotting scripts.

See [docs/transmission_pipeline.md](docs/transmission_pipeline.md) for a detailed
walk-through of the per-buoy CSMA pipeline, the scheduler's role, and per-mode
behaviour.

## Requirements

- Python ≥ 3.11
- [uv](https://github.com/astral-sh/uv) — manages the virtual environment and
  dependencies (matplotlib, pandas, pygame, pyyaml, scipy, tqdm)

## Installation

```sh
git clone https://github.com/Pierba/bnet_simulator.git
cd bnet_simulator
uv sync
```

`uv sync` creates the virtual environment and installs the locked dependencies.
You don't need to activate it manually — `uv run` does that for you.

## Quick start

Run the batch runner with the settings from [`config.yaml`](config.yaml):

```sh
uv run sim
```

This runs the configured sweep and writes results and plots under `metrics/`.
To stop early, press `Ctrl-C`; the runner cleans up its temporary files and exits.

## Batch runner flags

| Flag | Effect |
|------|--------|
| `-c`, `--compare` | Run every mode in `simulation.multihop_modes` on identical topologies and produce the cross-mode comparison plots. Without it, a single batch uses `simulation.multihop_mode`. |
| `-n`, `--no-plot` | Only generate the result CSVs, skip all plotting. Useful when collecting many runs to average later. |
| `-t TAG`, `--tag TAG` | Write this run's output under `metrics/<TAG>/` so repeated runs accumulate side by side (e.g. for `avg_metrics.py`). |

```sh
uv run sim --compare --tag run01
```

## Configuration

Every run is driven by [`config.yaml`](config.yaml). The most relevant keys:

### `simulation`

| Key | Meaning |
|-----|---------|
| `schedulers` | Schedulers to run (`static`, `dynamic_adab`, `dynamic_acab`). |
| `min_buoys` / `max_buoys` / `step_buoys` | Density sweep range and step. |
| `intervals` | Beacon intervals to sweep, in seconds. |
| `duration` | Simulated seconds per run. |
| `num_processes` | Parallel worker processes. |
| `ideal_channel` | If `true`, no collisions or losses. |
| `scenario` | `static`, `ramp`, or `random`. |
| `random_variability` | Fraction of buoys that can toggle per update (`random` scenario). |
| `enable_metrics` / `enable_logging` / `enable_file_logging` | Output toggles. |
| `multihop_mode` | Mode used for a single (non-compare) run. |
| `multihop_modes` | Modes swept and compared with `--compare`. |
| `multihop_limit` | Maximum hops for `forwarded` mode. |
| `append_hop_limit` | Hop horizon advertised in `append` mode (`1` = direct neighbours, `0` = unlimited). |
| `pending_queue_limit` | Maximum beacons held in the pending queue. |
| `forward_density_baseline` | `n0` for probabilistic forwarding: queue a beacon with `p = min(1, n0 / n_neighbors)`. |

### `world`, `buoys`, `network`, `csma`, `scheduler`

| Section | Controls |
|---------|----------|
| `world` | Simulation area `width` × `height` (metres). |
| `buoys` | Mobile fraction, default velocity, and Random-Waypoint speed/pause bounds. |
| `network` | Bit rate, propagation speed, communication range, and delivery probabilities. |
| `csma` | Slot time, DIFS time, and contention window. |
| `scheduler` | Static interval, beacon min/max interval, and the ADAB/ACAB thresholds and blend weights (`acab_weights` must sum to 1.0). |

## Output

Results land under `metrics/` (or `metrics/<tag>/` with `--tag`), organised per
interval, scenario, and multihop mode:

- `results_interval-*/` — per-run metric CSVs (one per scheduler × density).
- `plots_interval-*/` — per-mode plots of those metrics.
- `comparison_interval-*/` — cross-mode comparison histograms (when `--compare`
  sweeps more than one mode).

## Averaging multiple runs

Run the batch several times with distinct `--tag`s (and `--no-plot`), then average
and plot them together:

```sh
uv run src/script/avg_metrics.py \
  --input-dirs metrics/run01 metrics/run02 \
  --output-dir metrics/averaged
```

## Project structure

```text
bnet_simulator/
├── config.yaml              # Single source of truth for every run
├── pyproject.toml           # Project metadata & dependencies (uv / hatchling)
├── docs/
│   └── transmission_pipeline.md
└── src/
    ├── run.py               # `sim` entry point — sweep orchestrator / batch runner
    ├── config/
    │   └── config_handler.py    # Loads and exposes config.yaml
    ├── core/
    │   ├── events.py            # Event objects and the event queue
    │   ├── channel.py           # Shared wireless channel & propagation model
    │   └── simulator.py         # Discrete-event loop
    ├── buoys/
    │   └── buoy.py              # Buoy model, mobility, CSMA pipeline
    ├── protocols/
    │   ├── scheduler.py         # static / dynamic_adab / dynamic_acab schedulers
    │   └── beacon.py            # Beacon packet representation
    ├── script/
    │   ├── init.py              # Single-simulation entry (one per sweep task)
    │   ├── plot_metrics.py      # Per-mode result plots
    │   ├── plot_mode_comparison.py  # Cross-mode comparison histograms
    │   └── avg_metrics.py       # Average several tagged runs together
    └── utils/
        ├── metrics.py           # Metric collection & CSV output
        └── logging.py           # Logging helpers
```

## Documentation

- [docs/transmission_pipeline.md](docs/transmission_pipeline.md) — how a buoy
  decides what to transmit, when, and how a beacon travels through the CSMA
  pipeline across all three multihop modes.

## Contributing

1. Fork and clone the repository.
2. Create a feature branch: `git checkout -b my-feature`.
3. Make your changes and run a quick sweep (`uv run sim --no-plot`) to confirm
   nothing breaks.
4. Open a pull request describing the change and its motivation.

## Authors

- CesareDev — <corsicesare.lavoro@gmail.com>
- LuPierba — <filo.pierbatti3@gmail.com>

## License

No license file is currently included, so this is an academic research prototype
with all rights reserved by the authors. If you intend to reuse it, please contact
the authors first.
