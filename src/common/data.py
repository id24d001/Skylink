"""Loaders for the given data pack in data/. Raw files are never edited in place:
any cleaning a module needs is done in code and recorded in report/decision_log.md."""
import pandas as pd
import yaml

from .paths import DATA


def load_csv(name):
    """Read data/<name> (e.g. 'sites.csv') as a DataFrame."""
    return pd.read_csv(DATA / name)


def load_costs():
    """Read data/costs.yaml as a dict."""
    with open(DATA / "costs.yaml") as f:
        return yaml.safe_load(f)
