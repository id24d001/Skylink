"""Repo folders, resolved from this file so scripts work from any working directory."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
ORACLE = DATA / "oracle"
RESULTS = ROOT / "results"
FIGURES = ROOT / "figures"
