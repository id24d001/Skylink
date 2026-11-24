# Decision log

Every modelling decision, with the reason, and every AI suggestion accepted or rejected.
Format: **[module] decision** — why. Mark AI suggestions as *AI accepted* / *AI rejected* / *AI partly accepted*.

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
