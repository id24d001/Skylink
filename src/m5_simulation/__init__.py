"""M5 Discrete-event simulator (SimPy): one day of operations on an M4 network.

model.py   orders, drones, packs, bays, hubs; dispatch, grounding, headwind, outage; day KPIs
verify.py  Erlang C (M/M/c) check, sequential choice of days, hand-timed sortie, rule checks
live.py    live view: animated map of drones, queues and charging over one day (GIF)
run.py     run(ctx) for run_all.py
"""
from .model import (DaySim, HubSpec, Rules, Vehicle, hourly_profile, network_from_m4,  # noqa: F401
                    simulate_day, simulate_days, summarise, vehicle_for)
