# Decision log

Every modelling decision, with the reason, and every AI suggestion accepted or rejected.
Format: **[module] decision** — why. Mark AI suggestions as *AI accepted* / *AI rejected* / *AI partly accepted*.

## M0 — Problem framing

- **Three objectives = the three M6 objectives:** min annual cost, max S45, min road-fallback share. Using the same three in M0 and M6 keeps the chain consistent; P95 emergency time is reported only. *AI suggestion accepted* (also the partner's choice).
- **"45 min" split in two, so it is not both a constraint and an objective** (review point 3). Hard constraint C2: the farthest PHC, with no queue, is reached in ≤ 45 min (P17: 3 + 20.3 + 1 = 24.3 min at 15 m/s). The waiting part is in objective O2 (S45). *AI suggestion accepted; confirmed by the user.*
- **"Reliable" given a number:** a candidate system needs mean S45 ≥ 90 %; M7 then reports P(S45 ≥ 90 %) under ±25 % demand and 90–98 % availability. The partner kept 90 % out of M0; we use it as an acceptance floor, not as an objective. *AI suggestion accepted; confirmed by the user.*
- **"Controlled cost" given a number:** annual cost ≤ ₹5.57 crore, the cost of sending every order by road today (27.36 × 2,500 + 168.3 × 500 per day × 365). Cost itself stays objective O1. *AI suggestion accepted; confirmed by the user.*
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

## M2 — Hub plate topology

- **Out-of-plane bending model (Kirchhoff plate, ACM element), not plane stress.** In hover the arm thrust pushes up at the corners and the battery weight pushes down in the middle, so the plate bends. A plane-stress model would need an in-plane load that does not exist in hover. *AI suggestion accepted.* Rejected alternative: the partner's in-plane "multi-axial load cases". In-plane drag and yaw loads are an order of magnitude smaller and are carried by membrane action, so they are left out (stated on the card).
- **Boundary conditions stated explicitly.** w = 0 on a 10 × 10 mm arm-clamp patch at each corner, rotations free (simply supported, conservative), patches kept solid. The battery is a uniform downward pressure on the central 40 × 40 mm, F = 2 g × 9.48 kg = 186 N. 2 g comes from T_max = 2mg/n. 9.48 kg is the heaviest pack set in the M1 design set (E). The load magnitude only scales deflection and stress, not the topology or the compliance ratio. ⬜ User to confirm the 10 mm clamp patch (any 8–15 mm is defensible).
- **Density filter (Bruns–Tortorelli, top88 ft = 2), as the brief says.** The partner's card used a sensitivity filter; switched. r = 6 mm (physical, so it does not change with the mesh), p = 3, E_min = 1e-9, OC with move 0.2, stop at max change < 0.01.
- **Quarter-symmetry model.** Loads and supports are symmetric about both mid-lines. Solving a quarter (slope = 0 across the symmetry lines, filter mirrored) is about 8× faster, so the sweep fits in run_all.py on Colab. Checked: the quarter ×4 equals the whole-plate compliance.
- **A 6 mm filter radius does not prove 6 mm members** (review point). The outline step enforces it morphologically: threshold → open with a 6 mm disk → widen the thinner members by 3 mm → close → final open. This makes every solid point lie inside a solid 6 mm disk, which is the definition used. The narrowest member is then measured (6.75 mm). *AI partly accepted:* the first version deleted thin members (plain opening). That disconnected the arms from the centre at vf ≤ 0.33 (compliance ×10⁴–10⁶), so thin members are now widened instead.
- **The cut part is what gets checked, not the grey SIMP field.** The compliance ratio and the mass of the chosen design come from the thresholded, 6 mm-cleaned outline, analysed on a 1 mm mesh. The plate mass uses the cut part's own area fraction (0.332) in the brief's formula, not the nominal vf (0.32). At the same vf the cut part is stiffer than the SIMP field (2.31 vs 2.86 ×), because grey material is penalised by p = 3 and solid is not.
- **Design to 2.4× solid, not 2.5×.** The lightest design under 2.5× (vf 0.30, 106 g, 2.47×) moved to 2.50× on a finer design mesh. A 4 % margin costs 2.4 g. ⬜ User may prefer exactly 2.5× (106.3 g).
- **Sweep:** vf 0.20–0.50 in steps of 0.05 (the brief's range), then steps of 0.01 between the last failing and the first passing value (the review asked for this refinement).
- **Chosen plate:** vf 0.32, cut part 108.7 g (vs 250 g provisional, 247 g solid), compliance 2.31× solid, w_max 0.73 mm, peak bending stress 107 MPa vs 276 MPa yield at 2 g with the heaviest battery. The outline is in `results/m2_plate_outline.dxf` (mm, layer CUT) and `.svg`. No bolt holes are drawn: the arm clamps sit on the corner patches, and the battery strap goes around the central pad.
- **M1 re-run once with the M2 plate, on both FM paths.** run_all.py now runs M1 (0.25 kg) → M2 → M1 again (0.109 kg). M3's surrogate re-run then uses the M2 plate too, so everything downstream (M4, M6) gets post-M2 designs. Continuous optima fall by 229–536 g (FM0) and 232–347 g (surrogate). Snapped designs fall by 141–447 g, and every snapped design stays feasible. Part choices change for 4 of 5 designs on each path. `results/m2_m1_feedback.csv`.
- Note: the tables in `report/formulation_cards/M1.md` and the M3 section of the verification appendix still show the 0.25 kg plate values. The CSVs in `results/` are now post-M2.
- MBB benchmark run with top88's own inputs (60 × 20, vf 0.5, r_min 1.5) on the same SIMP/OC code. The plate code reuses that optimiser, with only the element changed.

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
- **Request 3 result (`data/oracle/result_3.csv`) — final hold-out, scored against the frozen predictions before refitting:**
  - Hold-out RMSE: GP 0.044, RBF cubic 0.051, thin-plate 0.059, quadratic 0.088.
  - Surprise 1: the random point (0.439 m, 26.87 N, DL 178) gave FM 0.686, but every model predicted about 0.59. FM stays high in the old DL gap and then drops very steeply between DL 178 and 217. The DL-gap pick suggested for request 2, which was declined, would have found this earlier.
  - Surprise 2: the cliff point (0.35 m, 33.24 N, DL 345) gave 0.205, so the drop is essentially complete by DL 345.
- **Final fit, 30 runs / 29 unique points.**
  - LOO RMSE: thin-plate 0.037, cubic 0.041, GP 0.041, quadratic 0.078.
  - Repeated 5-fold: thin-plate 0.046 ± 0.011, cubic 0.051 ± 0.010, GP 0.058 ± 0.018.
  - GP mean z² = 13.9, so its σ is badly over-confident.
  - The ranking between the RBFs and the GP flips depending on the test (LOO vs 5-fold vs hold-out). The differences are within the 5-fold spread.
- **Final surrogate kept as cubic RBF** (the user's earlier choice).
  - No test shows a clear winner.
  - The decisive check is that M1 does not care. Re-running M1 with thin-plate or GP instead changes the continuous masses by at most 1.6 % and gives the same snapped designs, except D: 11.61 kg with the GP vs 11.64 kg.
  - The GP is kept only for σ maps. Uncertainty of the cubic RBF is shown as a jackknife band (`jackknife_band`).
- **M1 optimum shift (with the M2 plate mass):** the real FM raises take-off mass by +4.0 % (A) to +8.2 % (D) relative to FM0 = 0.65, and shrinks rotors (C: 0.779 → 0.684 m). Request 3 moved the optimum by at most 0.6 % relative to the 24-run fit, so the surrogate has converged where M1 uses it.
- **Noise conclusion:** σ_noise ≈ 0.003 from one repeat, about 15× below the surrogate error, so the oracle is treated as practically deterministic and the models interpolate. Caveat: one pair only.
- **What a smooth model handles badly:** the cliff at DL above ~180 (FM 0.69 → 0.20). It is handled by spending request-3 points there, by limiting M1 to the convex hull of the samples, and by the fact that the M1 designs sit at DL 40–110, where models agree and the jackknife std is ≤ 0.02. FM in the cliff region is flagged as untrustworthy.
- Reference only: the GP on (log DL, D) scores 5-fold 0.039 ± 0.025 on 29 points, which is not clearly better any more. Keeping (D, T) is confirmed.
- Data provenance: `notebooks/M3_oracle_request1_LHS.ipynb` prints 15 points (seed 42) that do **not** match the points in `data/oracle/result_1.csv` (different seed or pyDOE version when the request was actually sent). `result_1.csv` is the source of truth; the notebook is kept as a record of the method only. ⬜ To fix: regenerate or annotate the notebook so its points match result_1.

## M4 — Hub network and fleet

Re-checked against the brief's M4 rules at the user's request. The first version (6.4 m/s design wind, 5 % road guard, peak-hour sizing, 70 % cap on bays) went beyond the brief and was replaced. The rejected items are kept below.

- **Design set: the surrogate-FM designs** (`m1_fm_designs.csv`, post-M2). The brief's M3 says "Re-run M1 with the surrogate FM", and the FM0 designs fall 1–2 km short with the real FM. *AI suggestion accepted; confirmed by the user.*
- **Every PHC must be served by a hub (Σ_j x_ij = 1).** The brief's variables have no road option for a PHC, its first line promises delivery to every PHC, and M0 made 24/24 coverage hard. Designs that cannot reach a PHC (A: 6 PHCs; B: P17) are infeasible. They are priced only in a what-if that sends just the unreachable PHCs by road, as M0 planned. *AI rejected (by the user):* the earlier "road allowed if ≤ 5 % of emergencies" guard. It was not in the brief and contradicted M0.
- **Reach = the still-air M1 radius with reserve: L_ij ≤ R.** The brief says "within your M1 range with reserve", and eq. (5) defines R in still air with the 15 % reserve in E_use. *AI rejected (by the user):* shrinking R for a 6.4 m/s P90 headwind. It was not in the M4 rule, and the M1 log had already rejected wind sizing. Wind risk is tested in M5 (hourly wind) and M7 (calm, typical, worst monsoon week). Note: the average-wind range loss (1 − 1/k, 5–9 %) is inside the 15 % reserve.
- **Capacity on mean daily demand: busy min/day ≤ 0.70 × 1440 × n_j × flyable share.** The brief says "at no more than 70 % utilization", and M5 says "The MILP uses averages". Queueing, the 10–11 h peak and waits over 30 min are M5's job, and M6 then tunes drones and bays. *AI rejected (by the user):* sizing every hour for the peak. It was not in the brief and gave 17–19 drones instead of 8–9.
- **Flyable share (0.945) kept in the capacity.** costs.yaml says no take-offs in an hour with gust ≥ 14 m/s, so a drone's usable time is the flyable hours.
- **Flight time and energy use the brief's headwind model at average wind.** "Use this for both energy and time." The factor V²/(V²−w²) is averaged over flyable hours (mean wind 4.0 m/s, factor 1.05–1.10 by design).
- **Busy time per sortie = flight + 2 min hover (eq. 5: 60 s at each end) + 10 min ground** (3 load/take-off + 1 drop + 6 swap/turnaround). The earlier version wrongly left out the hover.
- **Road fallback cost = emergencies placed in gust-grounded hours** (6.1 % of emergencies). They wait more than 30 min, and costs.yaml sends them by road. Routine ("same day") and lab orders wait for the next flyable hour and are still flown. The earlier version sent all classes by road, which over-counted.
- **Bays: no 70 % cap**, because the brief caps only drone time. Bays must recharge each day's sorties, with the outage rule from costs.yaml: a 3 kW genset runs only 2 × 1.5 kW bays, for 3 h × 3/7 = 1.29 h a day on average. Written as two linear constraints (24·b ≥ need, and (24 − 1.29)·b + 2 × 1.29 ≥ need).
- **Battery packs costed at one cycle per sortie** (₹22/Wh × E ÷ 500). Every sortie ends in a swap and a recharge. This counts under "drones", as packs come with the drone. The earlier version counted partial cycles by energy.
- **Energy price:** ₹28/kWh genset for the outage share of hours (1.29/24), ₹9/kWh otherwise.
- **o_j (operators per shift) and n_j ≥ y_j added.** o_j linearises "1 operator per 4 drones per shift, at least 1 per open hub" (costs.yaml). n_j ≥ y_j says a hub has at least one drone.
- **Solver: scipy.optimize.milp (HiGHS).** SciPy is already a dependency, and each design solves to proven optimality in under 1 s.
- **Brute-force sub-instance:** H0, H1, H3, P09 × P04–P08, P10, P22, P23, chosen because these assignments are contested (up to 2,916 of them). The brute force enumerates assignments, not (y, x) pairs, since opening an unused hub only adds cost.
- **Result:**
  - **D is cheapest at ₹1.69 crore a year**: 4 hubs (H1 H2 H6 P09, the 4-hub set M1 sized it for), 8 drones, 4 bays.
  - E costs ₹1.76 crore (3 hubs H1 H2 H4, as M1 intended). C costs ₹1.83 crore (5 hubs). A and B are infeasible (what-ifs ₹3.73 and ₹2.21 crore).
  - E saves a hub and 2 operator posts but pays for heavier drones and bigger packs. All are far below the ₹5.57 crore all-road cap.
  - The 8 drones are a floor from averages. M5/M6 will add drones and spares for the S45 target.

## M5 — Discrete-event simulator

Plan agreed with the user before building (all five open choices accepted as recommended). Every rule is taken from the brief's M5 section or costs.yaml; anything the brief leaves open is listed on the M5 card.

- **Tool: SimPy (4.1.1), not a hand-written event loop.** The brief names it ("SimPy or similar"). It is pure Python, 27 KB, no other dependencies, and installs in one line on Colab. A hand-written heap loop would be more code to verify. *AI suggestion accepted by the user.*
- **Live view: matplotlib animation saved as a GIF**, not a web dashboard. It needs no new dependency and runs inside `run_all.py`. *AI suggestion accepted.*
- **Network evaluated: design D as M4 sized it** (H1 H2 H6 P09, 8 drones, 4 bays), with C and E for comparison. A and B are infeasible in M4.
- **Day = 00:00–24:00 from an empty state**, wind from a random day of the year. **wind_days.csv day 1 = Monday**, which sets the outage days; the file has no weekday. *AI suggestion accepted by the user.*
- **Routine and lab orders still waiting at 24:00 go by road** ("same day"). The brief gives a road deadline only for emergencies (30 min). *AI suggestion accepted by the user.*
- **Spare packs = 0 by default**, as M4 costed it. A drone waits for a charged pack. Spares are an M6 decision. *AI suggestion accepted by the user.*
- **Reserve = round-half-up(25 % of a hub's drones)**, so 1 drone at hubs with 2 or more. The fraction is an M6 decision. *AI suggestion accepted by the user.*
- **A sortie out of range in this hour's headwind waits, as if grounded.** It uses the brief's 2LV/(V²−w²) model for energy, checked against E_use with the 15 % reserve. *AI suggestion accepted by the user.*
- **One sortie per order, and P09 served at P09 by hand-over**, both kept consistent with M4.
- **Outage: bays above 2 are pre-empted at 14:00** (3 kW genset ÷ 1.5 kW), as in M4. Design D has 1 bay per hub, so the outage changes the energy price there, not bay capacity.
- **Erlang C service time = M4's mean drone time at H2 (36.7 min)**, so the check runs at the scale the real model works at. Cases: c = 1–3, ρ = 0.5–0.9. *Run length raised to 150k orders* after the 60k run missed one CI by 0.5 min. The seed was not changed, to avoid picking a seed that passes.
- **Days: sequential pilot rule, then extended until every configuration's CI is < ±3 pp** (73 days). The same days and seeds are used for all configurations (common random numbers), which M6 needs to compare designs fairly. *Alternative considered:* all 365 days once. That is used only for the wind-ceiling check, since it costs 5× more per configuration.
- **Added (not asked for): a cause for every missed emergency** (grounding / headwind range / no drone or pack / late), and an unlimited-fleet "wind ceiling". These show what M6 can and cannot fix with fleet decisions.
- **Result to carry forward:**
  - D as M4: S45 0.851 ± 0.025 against the 0.90 target, which is below target, as predicted before running.
  - The prediction's reason was only partly right. The limit is not drone-minutes at the 10–15 h peak (utilisation is 0.46). It is packs and bays (no spares), the reserve, and wind.
  - Even an unlimited fleet reaches only 0.88–0.89 over the year. Grounding costs 5–6 % and headwind range 5–7 %. P11 and P17 from H2 sit within 0.1–1.5 km of D's still-air radius.
  - This is the cost of the still-air reach rule (M1/M4 decision). M6 cannot fix it with drones, bays or spares alone. Changing the vehicle (E) or the reach rule is an M6/M7 decision for the user.

## M6 — Simulation-based multi-objective optimisation

Plan sent to the user before building; all five open choices were confirmed as recommended. Every decision variable and objective is taken from the brief's M6 list; anything the brief leaves open is on the M6 card.

- **Designs C, D and E only; hubs and assignments fixed at M4's solution.** The brief says "on top of your M4 network", and A and B are infeasible in M4. *AI suggestion, confirmed by the user.*
- **Bounds:** drones from the M4 count to +3 per hub, spares ≤ 2 per drone, bays ≤ drones + spares, one reserve fraction r ∈ [0, 0.5]. The lower bound keeps M4's 70 % busy-time rule. +3 is a judgement bound that keeps the search small. It is active at a hub for some front points at the expensive end. M5's +10 fleet (unlimited) reached only S45 0.896 on 73 days, against 0.902 for the best M6 point, so wider bounds would not move the front by more than the noise. *AI suggestion, confirmed by the user.*
- **Spare-pack cost = price × 10 % a year** (capital tied up). Wear is already charged per sortie (500 cycles, as in M4), so a spare adds no wear. *AI suggestion, confirmed by the user.* *Alternative rejected:* annuitise each spare over the drone's 3-year life. That would also count wear a second time.
- **Road cost counts every road trip** (₹2,500 emergency, ₹500 routine/lab, costs.yaml), from the simulator. This exposes a gap in M4: M4 assumed every routine and lab order is flown, so its ₹1.69 cr/yr for D is optimistic. Simulated over 365 days, D as M4 sized it costs ₹3.34 cr/yr, and ₹1.9 cr of that is road trips.
- **S45 ≥ 0.90 is kept as an objective, not a constraint.** M5 showed wind caps S45 at about 0.88–0.89, so a constraint would leave nothing feasible. 0.90 is drawn as a reference line, and the choice of point is left to M7. *AI suggestion, confirmed by the user.*
- **Headwind range margin in M1 and an extra hub near P11/P17 are left out of M6.** They are not in the brief's M6 decision list (user rule: follow the brief strictly). They are logged as levers for M7's sensitivity and the memo.
- **Method: NSGA-II with common random numbers, not a 50-run surrogate** (the brief allows either). One configuration takes about 0.3 s per 73 days on 4 cores, so 5,000 runs fit in 25 min. *AI suggestion, confirmed by the user.*
- **Own NSGA-II (about 150 lines) instead of adding pymoo**, so every step is readable and defensible. *AI suggestion accepted.*
- **SBX / mutation distribution indices changed from the textbook 15 / 20 to 5 / 10.** *AI's first choice rejected after the ZDT1 check:* with 15 / 20 the engine stalled (gap 0.21 after 250 generations). With 5 / 10 the gap is 0.0024, and wider steps also suit the mostly integer M6 variables.
- **One NSGA-II run per design, fronts merged**, because each design has a different number of hubs (5, 4, 3) and so a different vector length. *Alternative:* one run with a categorical gene and padded slots. That is messier, with no gain for three designs.
- **The M4 sizing and "one spare per drone" were seeded into each design's first population**, so the search starts from the M5 baselines. Seeding cannot hide a better region, because the rest of the population is random.
- **Knee = the point farthest from the chord of the cost–S45 front** (normalised). It is the simplest rule that a non-engineer can see on the plot. *Alternatives for M7:* minimax regret, or best S45 within budget.
- **Weighted-sum check:** the NSGA-II front for the slice is every design-D point the two NSGA-II runs simulated, frozen before the weighted-sum runs. This keeps the check honest, because weighted-sum results cannot leak into the front they are compared with. **Result: 7/9 on or behind, 2 ahead by 0.07 and 0.18 pp of S45.** This is reported as a partial failure, not tuned away: the front is unconverged at the 0.1–0.2 pp level, which is far below the S45 noise (±2.5 pp).
- **Re-simulation on all 365 days with fresh seeds, instead of a surrogate check.** It found a 1.6 pp optimistic bias on the 73 search days, largest for D. **Final numbers are quoted on 365 days.** The knee's S45 is 0.889, not 0.901, so the M0 target is not met by any M6 configuration.
- **Result to carry forward to M7:**
  - Knee: design E, 11 drones (4/3/4), 15 spare packs, 7 bays and one reserve drone per hub. ₹2.25 cr/yr, S45 0.889 ± 0.015, road share 10 %, P95 150 min (365 days).
  - The cheapest front point is design D at ₹2.20 cr/yr with S45 0.863.
  - The main lever is spare packs and bays, not drones. Holding one reserve drone per hub trades about 1 pp more S45 for about 2 pp more road share.
  - Top-3 candidates for M7's robustness test: the knee (E), the highest-S45 E point (₹2.47 cr), and the cheap E point with no reserve (₹2.20 cr, S45 0.880).

## Repo
- One script `run_all.py` runs M0→M7 in order; a module joins the chain once it has `src/<module>/run.py` with `run(ctx)`. Unimplemented modules are skipped, so the chain always runs.
- Given data copied unchanged into `data/`; any cleaning is done in code and logged here.
- Course handout PDF kept out of the repo.
- **Every formulation card lists all its assumptions** (user rule), in its "Key assumptions and data issues handled" section: every modelling assumption and every choice the brief leaves open, each card still ≤ 1 page (checked by rendering to A4 at 10 pt).
