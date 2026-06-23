# Bachelor's thesis

`main.tex` — Context-Aware Adaptive Beaconing and Multi-Hop Awareness for
Infrastructure-Free Maritime Proximity Detection.

## Compile

```bash
pdflatex main.tex
pdflatex main.tex   # run twice for ToC / cross-references / bibliography
```

Needs a standard TeX distribution (TeX Live / MiKTeX). Packages used are all
mainstream: `graphicx, amsmath, amssymb, booktabs, caption, subcaption,
algorithm, algpseudocode, enumitem, siunitx, hyperref`.

## Figures

All figures in `figures/` are generated from the averaged simulation CSVs in
`../metrics/averaged/` by:

```bash
../.venv/Scripts/python.exe make_figures.py   # writes PDF + PNG into figures/
```

Re-run it after producing new averaged metrics to refresh every plot.

## Before submitting

Fill in the placeholders on the title page and in `\author{}`:
candidate name and ID number.
