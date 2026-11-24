# M3 — Oracle request 3: points and defence

File: `data/oracle/request_3.csv` (columns `student_id,D_m,T_N`; replace `ROLL_NUMBER` before sending).
Email subject: `ED6002 ORACLE <roll number> 3`. This is the last request (6 points, total 30).
Made by `notebooks/M3_step7_request3.ipynb` with `src/m3_surrogate/select_points.py`. These are the same score definitions as
request 2, fitted on the 23 unique points of requests 1 + 2.
Full table: `results/m3_request3_plan.csv`. Figure: `figures/m3_request3_scores.png`.
Frozen predictions (hold-out protocol): `results/m3_request3_frozen_predictions.csv`.

**Split (my choice): 2 exploitation + 3 exploration + 1 random, no repeat.** The request-2 repeat put the oracle
noise at about 0.003, roughly 10× below the model errors. A second repeat would add little.

**Exploitation targets** are the snapped M1 designs A–E after the re-run with the cubic-RBF FM, at loaded and empty
hover (`results/m1_fm_hover_points.csv`). Design C has weight 1 and the others 0.6.

**Exploitation scores are small this time.** The highest on the grid is 0.094, against 0.47 in request 2. The GP σ at
all 10 hover points is at most 0.004, about the size of the noise. That is evidence the surrogate is already good where M1
needs it. The two exploitation points top up the baseline design C and design D, where the remaining uncertainty sits.

| Point | D (m) | T (N) | DL (N/m²) | Category | Exploit | Explore | Dist. to existing | Why |
|---|---|---|---|---|---|---|---|---|
| R3-1 | 0.680 | 26.84 | 74 | Exploitation | 0.094 | 0.09 | 0.12 | Next to C-loaded (baseline design) |
| R3-2 | 0.575 | 28.62 | 110 | Exploitation | 0.089 | 0.14 | 0.15 | Next to D-loaded, near the FM peak (DL ~110) |
| R3-3 | 0.350 | 8.00 | 83 | Exploration | 0 | 0.90 | 0.13 | Small-rotor, low-thrust corner, highest σ left. GP and RBF disagree (0.65 vs 0.60) |
| R3-4 | 0.465 | 40.00 | 236 | Exploration | 0 | 0.81 | 0.23 | Start of the cliff at high thrust; no data between DL 225 and 416 |
| R3-5 | 0.350 | 33.24 | 345 | Exploration | 0 | 0.65 | 0.20 | Middle of the cliff (DL 345), between FM 0.47 (DL 225) and 0.20 (DL 416); models range 0.23 to 0.38 |
| R3-6 | 0.439 | 26.87 | 178 | Random | 0 | 0.09 | 0.12 | Uniform random (seed 6003), an unbiased check. It happens to fall in the old DL gap 151–217 |

**Hold-out protocol:** every model's prediction at these 6 points was saved before sending. When `result_3.csv` arrives,
the hold-out RMSE per model is computed against that file first, and only then is everything refitted on 30 runs.
