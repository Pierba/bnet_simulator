#!/usr/bin/env bash
#
# Run N independent comparison batches (CSV only) and average them into the
# final multihop-mode comparison plots.
#
# For each batch this invokes:  uv run src/run.py -n --tag run<NN>
#   -n      CSV only, skip all per-run plotting (every mode in
#           simulation.multihop_modes is swept regardless)
#   --tag   write under metrics/run<NN>/ so batches do not overwrite each other
#
# After all batches it runs avg_metrics.py over every metrics/run* directory,
# producing under <AveragedDir>:
#   results_interval-*_random_<mode>/   seed-averaged CSVs (with StdDev)
#   plots_interval-*_random_<mode>/     per-mode averaged plots (with error bars)
#   comparison_interval-*_random/       cross-mode comparison figures (the final plot)
#
# WHICH modes are compared (e.g. forwarded vs append) is controlled by
# simulation.multihop_modes in config.yaml, NOT by this script.
#
# Usage:
#   ./run_comparison_batch.sh [-r RUNS] [-a AVERAGED_DIR] [-c]
#
#   -r RUNS          Number of independent batches to run. Default 30.
#   -a AVERAGED_DIR  Output directory for averaged results/plots. Default metrics/averaged.
#   -c               Clean: remove existing metrics/run* dirs and the averaged dir
#                    before starting. Recommended for a fresh experiment.
#   -h               Show this help.
#
# Examples:
#   ./run_comparison_batch.sh
#   ./run_comparison_batch.sh -r 50 -c

set -euo pipefail

# Defaults
RUNS=30
AVERAGED_DIR="metrics/averaged"
CLEAN=0

usage() {
    sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while getopts ":r:a:ch" opt; do
    case "$opt" in
        r) RUNS="$OPTARG" ;;
        a) AVERAGED_DIR="$OPTARG" ;;
        c) CLEAN=1 ;;
        h) usage 0 ;;
        :) echo "Error: -$OPTARG requires an argument." >&2; usage 1 ;;
        \?) echo "Error: unknown option -$OPTARG." >&2; usage 1 ;;
    esac
done

# Colors (only if stdout is a terminal)
if [[ -t 1 ]]; then
    C_CYAN=$'\033[36m'; C_YELLOW=$'\033[33m'; C_GREEN=$'\033[32m'; C_RESET=$'\033[0m'
else
    C_CYAN=''; C_YELLOW=''; C_GREEN=''; C_RESET=''
fi

# Always operate from the repo root (this script's own location)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Validate Runs
if ! [[ "$RUNS" =~ ^[0-9]+$ ]] || [[ "$RUNS" -lt 1 ]]; then
    echo "Runs must be >= 1 (got '$RUNS')" >&2
    exit 1
fi

# Zero-pad tags to the width of the largest run number (min 2): run01, run02, ...
pad=${#RUNS}
(( pad < 2 )) && pad=2

if [[ "$CLEAN" -eq 1 ]]; then
    echo "${C_YELLOW}Cleaning previous run dirs and averaged output...${C_RESET}"
    # Remove metrics/run* directories (if any)
    while IFS= read -r -d '' d; do
        rm -rf -- "$d"
    done < <(find metrics -mindepth 1 -maxdepth 1 -type d -name 'run*' -print0 2>/dev/null)
    [[ -e "$AVERAGED_DIR" ]] && rm -rf -- "$AVERAGED_DIR"
fi

start_time=$(date +%s)
echo "${C_CYAN}Starting $RUNS comparison batches (CSV only)...${C_RESET}"

for (( i = 1; i <= RUNS; i++ )); do
    tag=$(printf "run%0${pad}d" "$i")
    echo
    echo "${C_CYAN}=== Batch $i / $RUNS  (tag: $tag) ===${C_RESET}"

    if ! uv run src/run.py -n --tag "$tag"; then
        echo "Batch $i (tag $tag) failed. Aborting." >&2
        exit 1
    fi
done

# Collect every run dir produced above (sorted for deterministic ordering)
mapfile -t runDirs < <(find metrics -mindepth 1 -maxdepth 1 -type d -name 'run*' | sort)

if [[ "${#runDirs[@]}" -eq 0 ]]; then
    echo "No metrics/run* directories found to average." >&2
    exit 1
fi

if [[ "${#runDirs[@]}" -ne "$RUNS" ]]; then
    echo "${C_YELLOW}WARNING: found ${#runDirs[@]} run dirs but ran $RUNS batches; averaging will include all of them. Use -c for a fresh experiment.${C_RESET}"
fi

echo
echo "${C_CYAN}Averaging ${#runDirs[@]} runs into $AVERAGED_DIR ...${C_RESET}"
uv run src/script/avg_metrics.py --input-dirs "${runDirs[@]}" --output-dir "$AVERAGED_DIR"

elapsed=$(( $(date +%s) - start_time ))
echo
echo "${C_GREEN}Done in $(( elapsed / 60 ))m $(( elapsed % 60 ))s.${C_RESET}"
echo "${C_GREEN}Averaged results & plots : $AVERAGED_DIR${C_RESET}"
echo "${C_GREEN}Final comparison figures : $AVERAGED_DIR/comparison_interval-*_random/${C_RESET}"
