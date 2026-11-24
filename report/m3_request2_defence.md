# M3 — Oracle request 2: points and defence (draft, rewrite in your own words)

File: `data/oracle/request_2.csv` (columns `student_id,D_m,T_N`; replace `ROLL_NUMBER` before sending).
Email subject: `ED6002 ORACLE <roll number> 2`.
Made by `notebooks/M3_step5_request2.ipynb` using `src/m3_surrogate/select_points.py`.
Full table with scores and predictions: `results/m3_request2_plan.csv`. Figure: `figures/m3_request2_scores.png`.

**Budget:** 15 used in request 1, 9 now, 6 kept for request 3 (to place at the designs after the M1 re-run
and wherever request 2 shows the surrogate is still weak).

**Split (my choice): 3 exploitation + 3 exploration + 2 random + 1 repeat.**

**Scores** (each in [0, 1], on a 91 × 91 grid over D 0.35–0.80 m, T 8–40 N, scaled to [0, 1]):
- Exploration = 0.7 · σ/σmax + 0.3 · |RBF cubic − GP| / max. GP σ says where the GP is unsure; the
  disagreement term catches places where the GP is confident but the second-best model says otherwise.
- Exploitation = closeness to the 10 M1 hover points × σ/σmax. Closeness = max over hover points of
  weight · exp(−d²/(2 · 0.08²)) with d the scaled distance; weight 1 for baseline design C, 0.6 for A, B, D, E.
  Multiplying by σ makes spots M1 needs but we already know well score low.
- Greedy: highest score among candidates ≥ 0.10 (scaled) from every existing and already-chosen point;
  σ recomputed after each pick with the point added (hyperparameters fixed; σ needs no FM value).

**M1 hover points already covered by request 1:** A-empty (0.737 m, 11.6 N) is 0.02 from pt 15 and E-loaded
(0.762 m, 38.5 N) is 0.05 from pt 2. The distance rule excludes candidates there automatically.

| Point | D (m) | T (N) | DL (N/m²) | Category | Exploit | Explore | Dist. to req. 1 | Why | GP now (±σ) | RBF now |
|---|---|---|---|---|---|---|---|---|---|---|
| R2-1 | 0.755 | 19.38 | 43 | Exploitation | 0.47 | 0.38 | 0.15 | Highest exploitation score: at C-empty (also near D-empty, A-loaded) and uncertain. | 0.586 ± 0.036 | 0.592 |
| R2-2 | 0.735 | 26.13 | 62 | Exploitation | 0.41 | 0.31 | 0.15 | Next highest: C-loaded / D-loaded, the baseline design's loaded hover. | 0.641 ± 0.031 | 0.632 |
| R2-3 | 0.790 | 38.93 | 79 | Exploitation | 0.23 | 0.39 | 0.11 | Nearest allowed spot to E-loaded (E-loaded itself is too close to pt 2); serves the heaviest design. | 0.651 ± 0.039 | 0.671 |
| R2-4 | 0.350 | 40.00 | 416 | Exploration | 0 | 1.00 | 0.30 | Highest-DL corner: largest σ and the models disagree most (GP 0.55 vs RBF 0.23). Shows how far the FM drop continues. | 0.545 ± 0.067 | 0.231 |
| R2-5 | 0.800 | 8.00 | 16 | Exploration | 0.04 | 0.70 | 0.20 | Lowest-DL corner: large σ, nothing measured below DL 27 (pt 15 was a worst LOO point). | 0.570 ± 0.059 | 0.479 |
| R2-6 | 0.410 | 8.00 | 61 | Exploration | 0 | 0.56 | 0.25 | Bottom-left edge, small rotor at low thrust: large σ, no point nearby. | 0.663 ± 0.054 | 0.660 |
| R2-7 | 0.644 | 21.17 | 65 | Random | 0.16 | 0.22 | 0.12 | Uniform random (seed 6002): an error check not chosen by the model (happens to be near B-loaded). | 0.632 ± 0.029 | 0.637 |
| R2-8 | 0.717 | 34.60 | 86 | Random | 0.04 | 0.17 | 0.12 | Uniform random (seed 6002), between D and E. | 0.685 ± 0.026 | 0.671 |
| R2-9 | 0.383 | 25.97 | 225 | Repeat | — | — | 0 | Repeat of pt 3 (FM 0.468): the difference is a direct measure of oracle noise; pt 3 had the worst GP LOO error and sits on the steep drop. | 0.468 (measured) | — |

Random points are redrawn (same seed sequence) if closer than 0.10 to any point, so they are uniform over the
part of the box not already sampled.

**Exploration did not pick the DL gap (151–217):** the GP is fairly confident along it, so σ there is lower than at
the corners. Reserving one exploration pick for the gap was suggested and declined; the gap can be revisited in
request 3 if the new data show a sharp change there.

**What would change request 3:** repeat differs by more than ~0.01 → noise is real; add a nugget floor and maybe
another repeat. R2-4/R2-5 far outside GP ±2σ → more points toward those corners. Random-point errors much larger
than LOO RMSE → LOO was optimistic. After the M1 re-run, the remaining 6 go to the designs' new hover points.
