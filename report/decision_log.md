# Decision log

Every modelling decision, with the reason, and every AI suggestion accepted or rejected.
Format: **[module] decision** — why. Mark AI suggestions as *AI accepted* / *AI rejected* / *AI partly accepted*.

## M0 — Problem framing

- **Three objectives = the three M6 objectives:** min annual cost, max S45, min road-fallback share. Using the same three in M0 and M6 keeps the chain consistent; P95 emergency time is reported only. *AI suggestion accepted* (also the partner's choice).
- **"45 min" split in two, so it is not both a constraint and an objective** (review point 3). Hard constraint C2: the farthest PHC, with no queue, is reached in ≤ 45 min (P17: 3 + 20.3 + 1 = 24.3 min at 15 m/s). The waiting part is in objective O2 (S45). *AI suggestion accepted.*
- **"Reliable" given a number:** a candidate system needs mean S45 ≥ 90 %; M7 then reports P(S45 ≥ 90 %) under ±25 % demand and 90–98 % availability. The partner kept 90 % out of M0; we use it as an acceptance floor, not as an objective. *AI suggestion accepted; user to confirm.*
- **"Controlled cost" given a number:** annual cost ≤ ₹5.57 crore, the cost of sending every order by road today (27.36 × 2,500 + 168.3 × 500 per day × 365). Cost itself stays objective O1. *AI suggestion accepted; user to confirm.*
- **Coverage is hard: 24 of 24 PHCs reachable by drone.** The brief's first line promises every PHC. So designs A and B (radius 10 and 14.6 km, cannot reach P17) are kept only as what-if cases that price this constraint.
- **Outage rule turned into a bay limit:** 3 kW genset / 1.5 kW per bay = at most 2 bays charging per hub in an outage.
- **Decision hierarchy drawn as 4 levels** (vehicle → hub network → fleet and policy → recommendation), with the vehicle design as a categorical variable at levels 2 and 3 (review point 4). The plate outline is a result of M2, not a decision; the SIMP densities and volume fraction are the continuous decisions.
- **hourly_profile.csv routine column sums to 1.1993 (planted).** Handled openly, not silently: demand.csv is treated as the true daily total, and each class's 24 fractions are rescaled to sum to 1 in code (`m0_framing.clean_hourly_profile`, returned as `ctx["hourly_profile"]` for M5). Alternatives rejected: using the raw column (inflates routine demand by 20 % in M5 but not in M4, so the two would disagree); guessing which hours are wrong (no evidence which ones). The partner advised "do not silently normalise"; rescaling with a logged check meets that.
- **Data audit in code** (`src/m0_framing/run.py` → `results/m0_data_checks.csv`, 18 checks, 2 flagged: the routine sum and 28 vs 30 tubes). Wind matches the brief's monthly table, batteries are 150 Wh/kg, tube masses match 1,550 kg/m³.
- *AI rejected:* reusing the partner's 4-page M0 card. It had pasted chat screenshots (against rule 1), no hierarchy, and wishes without numbers. Kept from it: the stakeholders, the three objectives and the 1.1993 catch.

## M1 — Vehicle MDO

- **Objective: min take-off mass for a required radius, not max radius.** Max radius drives D, E and d to their upper bounds (20.1 kg), with hover thrust of 49 N loaded and 43 N empty, outside the M3 oracle's 8–40 N. Drone capex also rises ₹45,000 per kg, and min-mass gives one design per required radius for M4/M6. *AI suggestion accepted* (proposed in the project chat; the user agreed).
- **Required radius from the map, not a guess.** Flight distance is the shortest path around both no-fly zones (visibility graph, zone corners pushed out 0.1 %). The farthest PHC from its nearest candidate hub is P17, at 18.3 km, so 18.4 km is the baseline ("cover-all"). 4 hubs need 19.7 km. 3 hubs need 23.9 km (reachable in still air, design E). 2 hubs need 33.4 km, which no design reaches. The code is in `src/m1_vehicle/target_radius.py`, and M4 can reuse it.
- **Size in still air, not for a headwind.** The brief defines R in still air (eq. 5), and the 15 % energy reserve in eq. (6) is the margin. Headwind is applied where routes and hours are known: M4 reachability, M5 simulation and M7 robustness, using the brief's model R(1 − w²/V²) (`m1_vehicle.model.radius_in_wind`; P90 flyable-hour mean wind = 6.4 m/s is available as `run.design_wind()`). *AI suggestion rejected (by the user):* sizing every design for a 6.4 m/s headwind (P90 of flyable hours). It made the baseline 1.6 kg heavier (11.18 vs 9.59 kg) and ruled out a 3-hub network. In still air, 3 hubs are reachable (design E, 24.0 km).
- **Constraint added: hover thrust 8–40 N loaded and empty**, so FM never has to be extrapolated when M3 re-runs M1. It is not active for any still-air design; the highest is design E at 38.5 N loaded.
- **Constraint added: T_max ≤ 100 N** (largest catalogue motor) in the continuous problem. After snapping, T_max must not exceed the chosen motor's own rating.
- **MDA tolerance 1e-12 kg inside the optimiser** (1e-8 asked for in the plan). It costs 3 more iterations, which is cheap. The gradient check shows that 1e-8 would also have been fine, since FD error is unchanged.
- **Complex-step gradients for SLSQP**, with FD used only as the check. Complex step has no step-size trade-off and passes exactly through the fixed-point iteration. When FM is a surrogate that cannot take complex input, the code falls back to central differences (`Params.complex_safe=False`).
- **Payload multiplier expressed two ways.** At fixed MTOW, +100 g costs 0.38 km (KKT 0.380, re-solve 0.381). For a built drone with a fixed battery, it costs 0.14 km. The first is the design trade-off the brief asks for. The second is what an operator sees.
- **Snapping = enumerate all catalogue combinations**, keeping the lightest feasible one, instead of rounding each variable to the nearest part. Rounding the partner's design gave an infeasible drone (3×B6S-32K = 2131 Wh > 2000 Wh; T_max 103.5 N > M100; f1 below limit). Props are whole inches, 14–31 in. V is re-chosen on a 0.5 m/s grid. Snapping adds 0.4–2.0 kg (5–15 %).
- **Five designs spread by required radius** (10, 14.6, 18.4, 19.7, 24.0 km → 7.2–15.7 kg, ₹5.2–9.1 lakh capex). The partner's five designs were nearly the same vehicle, so they gave M4 no cost-vs-reach choice. The radii are tied to network options: all PHCs but P17, all PHCs, all from 4 hubs, and all from 3 hubs.
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
- Step 3/4 result (user's run, inputs (D, T)): LOO RMSE quadratic 0.074 (Q² −0.24), RBF thin-plate 0.050 (smoothed 0.050), **RBF cubic 0.047 (Q² 0.50, best)**, GP 0.073 (Q² −0.18). Worst points 3, 15, 1, all at the DL extremes.
- GP finding: fitted nugget ≈ 0 (interpolates, nugget/signal ~ 2e-6) but LOO mean z² = 3.1, i.e. the GP is over-confident: its errors are ~1.8× larger than its σ claims. Cause is either oracle noise or shape the (D, T) kernel cannot follow; repeats in request 2 decide which.
- Known trade-off to revisit after request 2: the same GP on (log DL, D) inputs scored LOO RMSE ≈ 0.016 vs 0.073 on (D, T). Kept (D, T) for simplicity (user's choice); re-check with 24 points.
- ⬜ To fill: chosen surrogate for M1 and why; noise conclusion (after result_2).
- Request 2 first draft (hand-picked 9 points: 2 repeats, iso-DL line in the gap, corners, rough M1 region) was **superseded** once M1 was done; not sent.
- Before choosing, plotted GP σ, |RBF − GP| and the M1 hover points A–E (loaded/empty) over (D, T) to see the area of interest (`figures/m3_uncertainty_vs_M1_region.png`). Found: largest σ in the box corners, moderate σ in the M1 region, A-empty ≈ pt 15 and E-loaded ≈ pt 2 already measured.
- **User decision: request 2 = 3 exploitation + 3 exploration + 2 random + 1 repeat**, 9 points, keep 6 for request 3. Also user's request: choose by a reproducible scoring script, not by hand.
- *AI accepted*: the scoring rules in `src/m3_surrogate/select_points.py` — exploration = 0.7·σ + 0.3·|RBF−GP| (normalised); exploitation = closeness to M1 hover points (Gaussian, width 0.08 scaled; C weight 1, others 0.6) × σ; greedy picks with min distance 0.10 scaled and σ updated after each pick; random uniform with seed 6002; repeat of pt 3.
- *AI rejected (user)*: reserve one exploration pick for the DL gap 151–217. Declined; the score-based picks (three box corners/edges) were kept as they are.
- Request 2 points (defence in `report/m3_request2_defence.md`, figure `figures/m3_request2_scores.png`): exploitation (0.755, 19.38), (0.735, 26.13), (0.790, 38.93); exploration (0.350, 40.00), (0.800, 8.00), (0.410, 8.00); random (0.644, 21.17), (0.717, 34.60); repeat (0.383, 25.97). GP and RBF predictions recorded before sending.
- **Request 2 result (`data/oracle/result_2.csv`, committed with the user's OK; repo stays public):**
  - Hold-out check on the 8 new locations (predictions written before the oracle answered): RBF cubic RMSE 0.024, GP (D, T) 0.127 (0.038 without the corner).
  - Cliff: (0.35 m, 40 N), DL 416, gave FM 0.1995. GP (D, T) had predicted 0.545 ± 0.067 (z = −5.1). RBF cubic had predicted 0.231.
  - Low-DL corner (0.80, 8), DL 16, gave FM 0.492.
- **Noise:** the repeat of pt 3 gave 0.4683 then 0.4730 (difference 0.0047), so σ_noise ≈ 0.003. One pair is order-of-magnitude evidence only. This is about 10× smaller than the LOO errors, so the misfit is model shape, not oracle noise. The oracle is treated as near-deterministic: interpolating models are acceptable, and the GP nugget is allowed to stay small.
- **Repeats are averaged before fitting** (`average_repeats`): 24 runs become 23 unique points. Interpolating RBFs cannot take two values at one input, and LOO would be optimistic if the copy of a left-out point stayed in training.
- **LOOCV on 23 points (inputs D, T):** quadratic 0.080, RBF thin-plate 0.053, **RBF cubic 0.034 (Q² 0.90, best)**, GP 0.068 (Q² 0.59, mean z² 4.9, still over-confident). The worst point is the DL-416 cliff for most models.
- **Trade-off revisited (reference only, not adopted):** the same GP on (log DL, D) gives LOO 0.015 (Q² 0.98). Its D length scale goes to ~7.9, near the upper bound, so once DL is known, D barely matters. ⬜ User to decide whether to keep (D, T) for simplicity or switch the GP to (log DL, D).
- **Overfitting concern (raised by the user):** "is RBF cubic's Q² = 0.90 too good with 23 points?" Three checks were run (`notebooks/M3_step6_refit_after_request2.ipynb`, section 3b/4c):
  - **Repeated 5-fold CV** (20 shuffles, seed 0), RMSE mean ± std: RBF cubic 0.043 ± 0.008, thin-plate 0.062 ± 0.008, GP 0.078 ± 0.009, quadratic 0.081 ± 0.009. RBF cubic stays best when 20 % is held out. Its error rises from 0.034 (LOO) to 0.043, as expected with fewer training points.
  - **Without the cliff point**, the RBFs are tied within the spread: cubic 0.042 ± 0.013, thin-plate 0.045 ± 0.013, GP 0.053 ± 0.013. Cubic's lead comes mostly from handling the cliff.
  - **True hold-out** (fit on request 1 only, predict the 8 new request-2 locations): RBF cubic 0.024, thin-plate 0.070, quadratic 0.073, GP 0.127. Without the cliff point: thin-plate 0.015, cubic 0.023, GP 0.038, quadratic 0.071.
  - **Between the points:** no model goes below 0. RBF cubic exceeds the highest measured FM (0.695) by at most 0.010, on 0.06 % of the grid. Its roughness is similar to the GP's. No unphysical shape in DL 225–416 for any RBF; that region is simply unsampled.
  - **Conclusion:** no sign that RBF cubic overfits. Q² is inflated by the single FM 0.20 point widening the FM spread, so **RMSE is the headline metric** and Q² is secondary.
- **User decision: FM surrogate for M1 = RBF cubic** (best LOO 0.034, 5-fold 0.043, hold-out 0.024). `m3_surrogate/run.py` now passes it to M1 as `ctx["fm_surrogate"]` (via `FMFunction`). The GP is kept only for its σ map and adaptive sampling (`ctx["fm_gp"]`).
- **M1 re-run with the surrogate FM** (`src/m1_vehicle/fm_rerun.py`, called at the end of M3). FM is evaluated at the loaded and the empty hover thrust per rotor, in eqs. (2) and (4). Same 5 required radii as the FM0 run, still air. Gradients use central differences because the RBF cannot take complex input.
- **Guard against exploiting extrapolation (AI suggestion accepted):** both hover points (D, T) must lie inside the convex hull of the 23 oracle points. These are extra constraints, one per hull facet, for both loaded and empty. The guard is **inactive at every optimum** (smallest slack 0.054 of the box, at E snapped loaded), and the result is the same with or without it. Hover thrust stays within 8–40 N. Note: the hull edge at D = 0.80 runs through (0.799, 27.99), so a D-at-upper-bound design would have been pushed to about 0.799 m. None ended there.
- **Result: the optimum moved.** Mass rose 4–8 % (C: 9.59 → 10.33 kg). D fell to 0.52–0.76 m (was 0.66–0.80; no design is at the D bound any more). E rose 13–27 % and V rose 1–2 m/s. Why: big rotors at low disk loading have FM 0.50–0.60 in the oracle data, below 0.65, especially when empty. Smaller rotors gain FM but pay more induced power, so the optimum moves inward.
- **The FM0 designs fall short with the real FM:** their still-air radius drops 1.1–2.2 km (C: 18.4 → 16.3 km, so P17 would not be reachable). So the surrogate re-run matters for M4.
- **Surrogate-choice check:** the same problem with the GP mean as FM gives masses within 0.6 % of the RBF result, and 20 multistarts converge to one optimum. The move is not an RBF artefact.
- **Snapped designs (surrogate FM), all feasible:** A 7.25, B 8.68, C 10.92, D 11.78, E 15.38 kg. Snapping chose smaller props (20–27 in) where the surrogate FM is 0.63–0.70. E is lighter than its FM0 snap (15.72 kg): a 27 in prop at disk loading 102 N/m² has FM 0.69. ⬜ Use these as the M4/M6 design set, or keep FM0 (user to decide; recommended: surrogate set).
- **Request-3 hold-out protocol:** before sending request 3, freeze every model's predictions at the 6 points (`freeze_predictions` → `results/m3_request3_frozen_predictions.csv`). Score them against `result_3.csv` before refitting.
- **Request 3 (last 6 points), user's split: 2 exploitation + 3 exploration + 1 random, no repeat** (noise ≈ 0.003 already measured). Same scoring method as request 2, fitted on 23 unique points. Exploitation targets are the snapped surrogate-based M1 hover points (`results/m1_fm_hover_points.csv`). Random seed 6003. Defence in `report/m3_request3_defence.md`, figure `figures/m3_request3_scores.png`.
  - Exploitation scores came out small (max 0.094 vs 0.47 in request 2). GP σ at the M1 hover points is ≤ 0.004, so the surrogate is already good where M1 needs it.
  - Picks:
    - exploitation (0.680, 26.84) near C-loaded and (0.575, 28.62) near D-loaded
    - exploration (0.350, 8.00), (0.465, 40.00) DL 236, and (0.350, 33.24) DL 345, which covers the cliff between DL 225 and 416
    - random (0.439, 26.87)
  - Hold-out protocol applied: all model predictions frozen in `results/m3_request3_frozen_predictions.csv` before sending.
- Data provenance: `notebooks/M3_oracle_request1_LHS.ipynb` prints 15 points (seed 42) that do **not** match the points in `data/oracle/result_1.csv` (different seed or pyDOE version when the request was actually sent). `result_1.csv` is the source of truth; the notebook is kept as a record of the method only. ⬜ To fix: regenerate or annotate the notebook so its points match result_1.

## Repo
- One script `run_all.py` runs M0→M7 in order; a module joins the chain once it has `src/<module>/run.py` with `run(ctx)`. Unimplemented modules are skipped, so the chain always runs.
- Given data copied unchanged into `data/`; any cleaning is done in code and logged here.
- Course handout PDF kept out of the repo.
