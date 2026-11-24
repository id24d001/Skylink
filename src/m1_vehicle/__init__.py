"""M1: quadcopter vehicle MDO (mass-coupled sizing, MDF and IDF, catalogue snapping).

Typical use from other modules:

    from m1_vehicle import Params, Problem, evaluate, solve_mdf, resolve
    r = evaluate([0.8, 808, 0.04, 0.00167, 18.6])        # MDA + all outputs for one design
    x, r = resolve(18.4, m_plate=0.12)                    # M2: re-run with the new plate mass
    x, r = resolve(18.4, fm=my_fm, complex_safe=False)    # M3: re-run with FM(D, T) surrogate

The design set for M3/M4/M6 is results/m1_designs.csv (also ctx["m1_designs"]).
"""
from .model import Params, VARS, UNITS, LB, UB, analyse, evaluate, mda, fm_constant, radius_in_wind
from .mdo import Problem, solve_mdf, solve_idf, solve_max_radius
from .target_radius import flight_distance_matrix, hub_phc_distances
from .run import resolve, design_wind

__all__ = ["Params", "VARS", "UNITS", "LB", "UB", "analyse", "evaluate", "mda", "fm_constant", "radius_in_wind",
           "Problem", "solve_mdf", "solve_idf", "solve_max_radius",
           "flight_distance_matrix", "hub_phc_distances", "resolve", "design_wind"]
