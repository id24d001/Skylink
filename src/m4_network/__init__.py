"""M4 Hub network and fleet: hub-location / fleet-sizing MILP solved once per M1 design.

model.py   instance data (reach, busy time, costs) and the MILP (scipy.optimize.milp / HiGHS)
verify.py  brute force on a 4-hub x 8-PHC sub-instance, LP gap, big-M check, hand check
run.py     run(ctx) for run_all.py
"""
from .model import Settings, build_instance, solve, hub_table, assignment_table  # noqa: F401
