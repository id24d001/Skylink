"""Leave-one-out cross-validation: refit without point i, predict point i, repeat for all i."""
import copy
import numpy as np
import pandas as pd


def loo_predictions(model, X, y):
    """Return (mu_loo, sd_loo): prediction and std at each point from a model fitted without it.

    The model is refitted from scratch each time (for the GP this includes the hyperparameters),
    so the score is an honest estimate of error at an unseen point.
    For the GP, sd_loo includes the nugget, i.e. it is the std of a new oracle value.
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    n = len(y)
    mu = np.empty(n)
    sd = np.full(n, np.nan)
    for i in range(n):
        keep = np.arange(n) != i
        m = copy.deepcopy(model).fit(X[keep], y[keep])
        if hasattr(m, "gp_"):
            mu_i, sd_i = m.predict(X[i:i + 1], return_std=True, include_noise=True)
            sd[i] = sd_i[0]
        else:
            mu_i = m.predict(X[i:i + 1])
        mu[i] = mu_i[0]
    return mu, sd


def loo_table(results, y):
    """results: dict name -> (mu_loo, sd_loo). Returns one row per model with error metrics."""
    y = np.asarray(y, dtype=float)
    rows = []
    for name, (mu, sd) in results.items():
        e = y - mu
        row = {"model": name,
               "LOO RMSE": np.sqrt(np.mean(e**2)),
               "LOO MAE": np.mean(np.abs(e)),
               "max |error|": np.max(np.abs(e)),
               "worst pt": int(np.argmax(np.abs(e))) + 1,
               "LOO Q2": 1 - np.sum(e**2) / np.sum((y - y.mean())**2)}
        if np.all(np.isfinite(sd)):
            z = e / sd
            row["mean z^2"] = np.mean(z**2)      # ~1 if the GP's uncertainty is honest
        rows.append(row)
    return pd.DataFrame(rows).set_index("model")


def repeated_kfold(model, X, y, k=5, repeats=20, seed=0):
    """Repeated k-fold CV: shuffle, split into k folds, refit on k-1 folds, predict the held-out fold.

    Holds out ~20 % of the points at a time (harder than LOO, which holds out one), repeated with
    different shuffles so the result does not depend on one lucky split.
    Returns an array of RMSE values, one per repeat.
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    n = len(y)
    rng = np.random.default_rng(seed)
    rmse = []
    for _ in range(repeats):
        folds = np.array_split(rng.permutation(n), k)
        pred = np.empty(n)
        for f in folds:
            keep = np.setdiff1d(np.arange(n), f)
            m = copy.deepcopy(model).fit(X[keep], y[keep])
            pred[f] = m.predict(X[f])
        rmse.append(np.sqrt(np.mean((y - pred)**2)))
    return np.array(rmse)


def holdout_errors(models, X_train, y_train, X_test, y_test):
    """Fit each model on the training points only and score it on new points it never saw.

    This is the cleanest overfitting test: the test points come from a later oracle request.
    Returns (table, predictions) with one row / column per model.
    """
    y_test = np.asarray(y_test, dtype=float)
    rows, preds = [], {}
    for name, model in models.items():
        m = copy.deepcopy(model).fit(X_train, y_train)
        p = m.predict(X_test)
        e = y_test - p
        preds[name] = p
        rows.append({"model": name, "hold-out RMSE": np.sqrt(np.mean(e**2)),
                     "hold-out MAE": np.mean(np.abs(e)), "max |error|": np.max(np.abs(e))})
    return pd.DataFrame(rows).set_index("model"), pd.DataFrame(preds)


def shape_checks(model, D_grid, T_grid, make_features, fm_max_observed):
    """Look between the points: values a physical FM should not take, and how wiggly the surface is.

    FM must lie in (0, 1); an interpolant that overshoots goes above the highest FM ever measured or
    below 0. Roughness = mean |d2FM/dT2| on the grid in 1/N^2 (larger = more wiggles).
    """
    Dg, Tg = np.meshgrid(D_grid, T_grid)
    F = model.predict(make_features(Dg.ravel(), Tg.ravel())).reshape(Dg.shape)
    dT = T_grid[1] - T_grid[0]
    d2 = np.abs(np.diff(F, n=2, axis=0)).mean() / dT**2
    return {"min FM": F.min(), "max FM": F.max(),
            "% grid FM < 0": 100 * np.mean(F < 0),
            f"% grid FM > max observed": 100 * np.mean(F > fm_max_observed + 0.01),
            "roughness mean|d2FM/dT2| (1/N2)": d2}


def freeze_predictions(models, X_fit, y_fit, D_new, T_new, make_features, path):
    """Hold-out protocol for the next oracle request: BEFORE the oracle answers, fit every model on
    the current data and save its prediction at the requested points. When the results arrive,
    compare against this file (do not refit first). Returns the saved table."""
    Xn = make_features(D_new, T_new)
    out = pd.DataFrame({"D_m": np.asarray(D_new, float), "T_N": np.asarray(T_new, float)})
    for name, model in models.items():
        m = copy.deepcopy(model).fit(X_fit, y_fit)
        out[name] = m.predict(Xn)
    out.to_csv(path, index=False, float_format="%.5f")
    return out
