# BNet Simulator

Discrete-event simulator for testing and comparing beacon-scheduling and multihop
strategies in a simulated buoys network. It sweeps buoy densities and beacon intervals,
runs each combination per scheduler, and produces metric CSVs and comparison plots.

## Requirements

- [uv](https://github.com/astral-sh/uv)

## Usage

Clone the repo, `cd` into it, and run the batch runner:

```sh
uv run sim
```

This reads [`config.yaml`](config.yaml), runs the configured sweep, and writes results
and plots under `metrics/`.

### Batch runner flags

| Flag | Effect |
|------|--------|
| `-c`, `--compare` | Run every mode in `simulation.multihop_modes` on identical topologies and produce the cross-mode comparison plots. Without it, a single batch uses `simulation.multihop_mode`. |
| `-n`, `--no-plot` | Only generate the result CSVs, skip all plotting. Useful when collecting many runs to average later. |
| `-t TAG`, `--tag TAG` | Write this run's output under `metrics/<TAG>/` so repeated runs accumulate side by side (e.g. for `avg_metrics.py`). |

## Concepts

- **Schedulers** (`simulation.schedulers`): `static` (fixed interval), `dynamic_adab`
  (density-driven backoff), `dynamic_acab` (density + contact-recency + mobility blend).
- **Scenarios** (`simulation.scenario`): `static` (fixed buoy count), `ramp` (buoys
  activate one at a time), `random` (buoys randomly activate/deactivate over time).
- **Multihop modes** (`simulation.multihop_mode`): `none` (1-hop), `append` (piggyback
  learned topology onto own beacons), `forwarded` (re-broadcast others' beacons with a TTL).

See [docs/transmission_pipeline.md](docs/transmission_pipeline.md) for a detailed walk
through the per-buoy CSMA pipeline, the scheduler's role, and per-mode behaviour.

## Averaging multiple runs

Run the batch several times with distinct `--tag`s (and `--no-plot`), then average and
plot them together:

```sh
uv run src/script/avg_metrics.py --input-dirs metrics/run01 metrics/run02 --output-dir metrics/averaged
```
