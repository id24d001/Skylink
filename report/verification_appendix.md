# Verification appendix

One figure or table per module. Figures and tables are written by `python run_all.py`
into `figures/` and `results/`.

| Module | Check required by the brief | Evidence | Status |
|---|---|---|---|
| M0 | Formulation card review (no code check) | — | ⬜ |
| M1 | MDA convergence plot; finite-difference gradient check at two step sizes; MDF vs IDF within 1% | `figures/m1_mda_convergence.png`, `figures/m1_gradient_check.png`, `results/m1_gradient_check.csv`, `results/m1_mdf_vs_idf.csv`, `results/m1_hand_check.csv` | ✅ |
| M2 | MBB-beam benchmark reproduced; no-filter and p = 1 cases explained | — | ⬜ |
| M3 | LOOCV error table; parity plot; note on oracle noise | `results/m3_loo_table.csv`, `figures/m3_loo_parity.png` | 🟡 request 1 only |
| M4 | Brute force on 4-hub, 8-PHC sub-instance matches solver; LP-relaxation gap; big-M justified | — | ⬜ |
| M5 | One hub, exponential service, no wind/outage vs Erlang C; days chosen so S45 95% CI < ±3 pp | — | ⬜ |
| M6 | Weighted-sum points on or behind NSGA-II front (2-objective slice); re-simulated front vs surrogate | — | ⬜ |
| M7 | Robustness / reliability / sensitivity tables | — | ⬜ |

## M1 — Vehicle MDO

**MDA convergence.** Fixed-point iteration on mass from 1, 10 and 40 kg. The error falls by 0.078 per step
(the slope of eq. 1 in m), as predicted, and reaches 1e-8 kg in 9–10 iterations.

![M1 MDA convergence](../figures/m1_mda_convergence.png)

**Analysis hand check.** A line-by-line `math` re-implementation of eqs. (1)–(6), with the mass equation solved in
closed form, matches the code to a relative error of 1e-14 on all 5 designs. It also matches the partner's max-radius
design (20.149 kg, R = 27.27 km, not 29.49 km). See `results/m1_hand_check.csv`.

**Gradient check** (`results/m1_gradient_check.csv`). The optimiser uses complex-step gradients. Max relative error of finite differences against them:

| point | function | forward 1e-4 | forward 1e-6 | central 1e-4 | central 1e-6 |
|---|---|---|---|---|---|
| baseline optimum | objective m | 2.4e-5 | 2.4e-7 | 2.3e-10 | 9.7e-10 |
| baseline optimum | constraints | 9.4e-3 | 9.4e-5 | 8.2e-7 | 1.2e-8 |
| random point | objective m | 3.3e-5 | 3.2e-7 | 4.3e-10 | 1.5e-9 |
| random point | constraints | 2.5e-4 | 2.5e-6 | 5.0e-8 | 4.8e-9 |

Forward-difference error falls 100× when h falls 100× (truncation, O(h)), and central differences are O(h²) until
round-off. The step sweep below shows the classic V, with the minimum near h = 1e-7. Loosening the MDA tolerance from 1e-12
to 1e-8 barely changes this: the iterates are smooth in x because the iteration count does not change between x and x + h.

![M1 gradient check](../figures/m1_gradient_check.png)

**MDF vs IDF** (`results/m1_mdf_vs_idf.csv`). 8 starts each, and all 8 converge to the same optimum for every design:

| design | R_req km | m MDF kg | m IDF kg | difference |
|---|---|---|---|---|
| A light | 10.0 | 7.1954 | 7.1954 | < 1e-9 % |
| B all-but-one | 14.6 | 9.0372 | 9.0372 | < 1e-9 % |
| C cover-all | 18.4 | 11.1824 | 11.1824 | < 1e-9 % |
| D 4-hub | 19.7 | 12.1376 | 12.1376 | < 1e-9 % |
| E reach | 22.7 | 15.2508 | 15.2508 | < 1e-9 % |

**Multiplier check.** The KKT payload multiplier is 0.281 km per 100 g; a direct re-solve gives 0.283 km.
dm*/dm_pay is 1.892 (re-solve 1.895), and dm*/dR is 0.673 kg/km (re-solve 0.677).

**Snapped designs re-checked.** Every constraint was re-run with catalogue masses and the chosen motor's rating. All 5 are feasible
(`results/m1_designs.csv`, columns `g: ...`).

![M1 trade-off](../figures/m1_tradeoff.png)

## M3 — Rotor surrogate

![M3 LOO parity](../figures/m3_loo_parity.png)

LOOCV table: `results/m3_loo_table.csv` (each fold refits the model from scratch, GP hyperparameters included).

Noise note: _to write after the user's own run (GP nugget vs signal variance, mean z²; no repeated points yet)._
