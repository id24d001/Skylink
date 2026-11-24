"""M3: figure-of-merit surrogate FM(D, T) built from oracle data.

Typical use (also from M1 and run_all.py):

    from m3_surrogate import load_oracle, make_features, GPSurrogate
    df = load_oracle("data/oracle")          # reads every result_*.csv
    X = make_features(df.D_m, df.T_N)
    gp = GPSurrogate().fit(X, df.FM.values)
    fm, sd = gp.predict(make_features(0.5, 20.0), return_std=True)
"""
from .data import (D_MIN, D_MAX, T_MIN, T_MAX,
                   load_oracle, disk_loading, make_features)
from .models import QuadraticSurrogate, RBFSurrogate, GPSurrogate
from .validation import loo_predictions, loo_table
from .adaptive import candidate_grid, trend_std, greedy_select, write_request_csv

__all__ = ["D_MIN", "D_MAX", "T_MIN", "T_MAX",
           "load_oracle", "disk_loading", "make_features",
           "QuadraticSurrogate", "RBFSurrogate", "GPSurrogate",
           "loo_predictions", "loo_table",
           "candidate_grid", "trend_std", "greedy_select", "write_request_csv"]
