"""M2: hub plate topology (SIMP with a density filter, plate bending) and waterjet outline.

    from m2_plate import PlateSpec, build, optimise
    p, k = build(PlateSpec(m_batt=2.4), n=40)      # quarter model, 2 mm elements
    r = optimise(p, vf=0.35, penal=3, rmin=3)      # rmin in elements (6 mm / 2 mm)

run.run(ctx) does the sweep, picks the plate, writes the outline and re-runs M1 with the new mass.
"""
from .fe import Grid, ke_q4, ke_acm, density_filter
from .simp import Problem, optimise, grey_index
from .plate import PlateSpec, build, quarter_to_full
from .outline import make_outline

__all__ = ["Grid", "ke_q4", "ke_acm", "density_filter", "Problem", "optimise", "grey_index",
           "PlateSpec", "build", "quarter_to_full", "make_outline"]
