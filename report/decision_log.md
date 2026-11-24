# Decision log

Every modelling decision, with the reason, and every AI suggestion accepted or rejected.
Format: **[module] decision** — why. Mark AI suggestions as *AI accepted* / *AI rejected* / *AI partly accepted*.

## M1 — Vehicle MDO

- **Objective: min take-off mass for a required radius, not max radius.** Max radius drives D, E and d to their upper bounds (20.1 kg), with hover thrust of 49 N loaded and 43 N empty, outside the M3 oracle's 8–40 N. Drone capex also rises ₹45,000 per kg, and min-mass gives one design per required radius for M4/M6. *AI suggestion accepted* (proposed in the project chat; the user agreed).
- **Required radius from the map, not a guess.** Flight distance is the shortest path around both no-fly zones (visibility graph, zone corners pushed out 0.1 %). The farthest PHC from its nearest candidate hub is P17, at 18.3 km, so 18.4 km is the baseline ("cover-all"). 4 hubs need 19.7 km. 3 hubs need 23.9 km, which no design reaches, so M4 needs at least 4 hubs. The code is in `src/m1_vehicle/target_radius.py`, and M4 can reuse it.
- **Size for a 6.4 m/s headwind** (P90 of the hourly mean wind in hours with gust < 14 m/s), using the brief's headwind model: R_wind = R(1 − w²/V²). This costs about 1.6 kg at the baseline (9.59 kg in still air vs 11.18 kg). Alternative: size for still air and let M5/M7 handle wind. Rejected, because a drone sized for still air would fall short of its radius whenever there is any headwind; with this choice it covers 90 % of flyable hours. *AI suggestion, accepted by me / ⬜ user to confirm.*
- **Constraint added: hover thrust 8–40 N loaded and empty**, so FM never has to be extrapolated when M3 re-runs M1. It is active only for design E (39.9 N loaded).
- **Constraint added: T_max ≤ 100 N** (largest catalogue motor) in the continuous problem. After snapping, T_max must not exceed the chosen motor's own rating.
- **MDA tolerance 1e-12 kg inside the optimiser** (1e-8 asked for in the plan). It costs 3 more iterations, which is cheap. The gradient check shows that 1e-8 would also have been fine, since FD error is unchanged.
- **Complex-step gradients for SLSQP**, with FD used only as the check. Complex step has no step-size trade-off and passes exactly through the fixed-point iteration. When FM is a surrogate that cannot take complex input, the code falls back to central differences (`Params.complex_safe=False`).
- **Payload multiplier expressed two ways.** At fixed MTOW, +100 g costs 0.28 km (KKT 0.281, re-solve 0.283). For a built drone with a fixed battery, it costs 0.11 km. The first is the design trade-off the brief asks for. The second is what an operator sees.
- **Snapping = enumerate all catalogue combinations**, keeping the lightest feasible one, instead of rounding each variable to the nearest part. Rounding the partner's design gave an infeasible drone (3×B6S-32K = 2131 Wh > 2000 Wh; T_max 103.5 N > M100; f1 below limit). Props are whole inches, 14–31 in. V is re-chosen on a 0.5 m/s grid. Snapping adds 0.3–1.2 kg (4–10 %).
- **Five designs spread by required radius** (10, 14.6, 18.4, 19.7, 22.7 km → 7.5–16.3 kg, ₹5.4–9.3 lakh capex). The partner's five designs were nearly the same vehicle, so they gave M4 no cost-vs-reach choice. The radii are tied to network options: all PHCs but P17, all PHCs, all from 4 hubs, and the longest buildable range.
- **Partner's M1 numbers not reused.** The independent check (`reviews/m1_independent_check.py`) and the new code agree: 20.149 kg and R = 27.27 km at the max-radius design. The card's 29.49 km came from a wrong radius/cruise power implementation.
- Data note: components.csv has 28 tubes, not 30 as the brief says (CF35x0.75 and CF40x0.75 are missing). Snapping uses the 28 that exist.

## M3 — Rotor surrogate

- Request 1: 15-point maximin LHS over D 0.35–0.80 m, T 8–40 N (min scaled distance 0.184 vs 0.088 plain LHS).
- Exploration: disk loading DL = T/(πD²/4) explains FM better than (D, T) (LOO RMSE 0.059 vs 0.074). FM peaks near DL 115–150, drops steeply above ~215. Gap DL 151–217 unsampled.
- AI suggestion (partly) accepted: target request 2 at the DL gap, extremes, repeats, and M1 operating points.
- Step 3/4 setup: fixed input scaling from the oracle limits (not the data), so adding result_2/3 does not change it.
- Models compared by LOOCV with full refit per fold (GP hyperparameters re-optimised each time): quadratic (D, T) baseline, RBF thin-plate (exact and smoothing=1e-3, hand-picked, not tuned), RBF cubic, GP with ARD length scales + fitted nugget.
- Noise check: GP nugget vs signal variance + standardised LOO residuals (mean z²). Caveat: no repeats yet, so nugget cannot separate noise from sharp real features.
- AI suggestion accepted: put fitting code in src/m3_surrogate/ so M1 re-run and run_all.py import the same functions.
- AI suggestion rejected: (log DL, D) as surrogate inputs. Chosen instead: plain scaled (D, T) for all models, because D spans only ~2.3× and T ~5×, so a log transform is not needed and (D, T) is simpler to explain. DL kept only for interpretation (plots, report). Note from the test run: LOO errors are higher in (D, T) than with (log DL, D), most for the GP; to be weighed after the user's own run.
- ⬜ To fill after running: chosen surrogate for M1 and why; noise conclusion.
- Data provenance: `notebooks/M3_oracle_request1_LHS.ipynb` prints 15 points (seed 42) that do **not** match the points in `data/oracle/result_1.csv` (different seed or pyDOE version when the request was actually sent). `result_1.csv` is the source of truth; the notebook is kept as a record of the method only. ⬜ To fix: regenerate or annotate the notebook so its points match result_1.

## Repo
- One script `run_all.py` runs M0→M7 in order; a module joins the chain once it has `src/<module>/run.py` with `run(ctx)`. Unimplemented modules are skipped, so the chain always runs.
- Given data copied unchanged into `data/`; any cleaning is done in code and logged here.
- Course handout PDF kept out of the repo.
