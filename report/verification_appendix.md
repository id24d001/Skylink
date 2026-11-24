# Verification appendix

One figure or table per module. Figures and tables are written by `python run_all.py`
into `figures/` and `results/`.

| Module | Check required by the brief | Evidence | Status |
|---|---|---|---|
| M0 | Formulation card review (no code check) | — | ⬜ |
| M1 | MDA convergence plot; finite-difference gradient check at two step sizes; MDF vs IDF within 1% | — | ⬜ |
| M2 | MBB-beam benchmark reproduced; no-filter and p = 1 cases explained | — | ⬜ |
| M3 | LOOCV error table; parity plot; note on oracle noise | `results/m3_loo_table.csv`, `figures/m3_loo_parity.png` | 🟡 request 1 only |
| M4 | Brute force on 4-hub, 8-PHC sub-instance matches solver; LP-relaxation gap; big-M justified | — | ⬜ |
| M5 | One hub, exponential service, no wind/outage vs Erlang C; days chosen so S45 95% CI < ±3 pp | — | ⬜ |
| M6 | Weighted-sum points on or behind NSGA-II front (2-objective slice); re-simulated front vs surrogate | — | ⬜ |
| M7 | Robustness / reliability / sensitivity tables | — | ⬜ |

## M3 — Rotor surrogate

![M3 LOO parity](../figures/m3_loo_parity.png)

LOOCV table: `results/m3_loo_table.csv` (each fold refits the model from scratch, GP hyperparameters included).

Noise note: _to write after the user's own run (GP nugget vs signal variance, mean z²; no repeated points yet)._
