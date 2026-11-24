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
