"""Loading oracle results and turning (D, T) into surrogate inputs."""
from pathlib import Path
import numpy as np
import pandas as pd

# Oracle limits (assignment, "Figure of merit")
D_MIN, D_MAX = 0.35, 0.80     # rotor diameter, m
T_MIN, T_MAX = 8.0, 40.0      # thrust per rotor, N


def disk_loading(D, T):
    """DL = T / A with A = pi D^2 / 4  (N/m^2)."""
    D = np.asarray(D, dtype=float)
    T = np.asarray(T, dtype=float)
    return T / (np.pi * D**2 / 4)


def load_oracle(folder_or_files):
    """Read oracle results and stack them into one table.

    folder_or_files: a folder (reads every result_*.csv in it, sorted) or a list of csv paths.
    Returns a DataFrame with D_m, T_N, FM, batch (file name), DL, and a 1-based index 'pt'.
    """
    p = folder_or_files
    if isinstance(p, (str, Path)) and Path(p).is_dir():
        files = sorted(Path(p).glob("result_*.csv"))
    elif isinstance(p, (str, Path)):
        files = [Path(p)]
    else:
        files = [Path(f) for f in p]
    if not files:
        raise FileNotFoundError(f"no result_*.csv found in {p}")

    parts = []
    for f in files:
        d = pd.read_csv(f)
        d = d[["D_m", "T_N", "FM"]].copy()      # ignore extra columns such as student_id
        d["batch"] = f.stem
        parts.append(d)
    df = pd.concat(parts, ignore_index=True)
    df["DL"] = disk_loading(df.D_m, df.T_N)
    df.index = np.arange(1, len(df) + 1)
    df.index.name = "pt"
    return df


def make_features(D, T):
    """Surrogate inputs: x1 = scaled D, x2 = scaled T, both in [0, 1] over the oracle box.

    Scaling uses the oracle limits (not the data), so it stays the same when result_2/3 are added.
    Accepts scalars or arrays; returns an (n, 2) array.
    """
    D = np.atleast_1d(np.asarray(D, dtype=float))
    T = np.atleast_1d(np.asarray(T, dtype=float))
    x1 = (D - D_MIN) / (D_MAX - D_MIN)
    x2 = (T - T_MIN) / (T_MAX - T_MIN)
    return np.column_stack([x1, x2])
