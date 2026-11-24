# SkyLink: Designing a Medical Drone Delivery Network (ED6002)

Repository: <https://github.com/id24d001/Skylink>

SkyLink designs a drone network that delivers blood, antivenom, routine medicines and lab samples
to the 24 primary health centres (PHCs) of Kaveripuram district. The project runs in eight modules
(M0–M7), each feeding the next: the vehicle is sized (M1) with a topology-optimised hub plate (M2)
and a rotor surrogate built from 30 oracle runs (M3); hubs and fleet are placed by a MILP (M4);
a discrete-event simulator (M5) tests them against a year of hourly wind; NSGA-II searches fleet
and policy on that simulator (M6); and M7 stress-tests the finalists and makes the recommendation.

**Recommendation (M7):** design E with three hubs (H1, H2, H4), 11 drones, 14 spare packs,
9 charging bays and one drone per hub reserved for emergencies: ₹2.25 crore a year (40 % of
all-road delivery) with 88.9 % of emergencies delivered within 45 minutes over a typical year.

## How to run

Python 3.10 or newer.

```bash
git clone https://github.com/id24d001/Skylink.git
cd Skylink
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python run_all.py            # the whole chain, M0 -> M7 (about 52 min on 4 cores)
python run_all.py --fast     # quick end-to-end check with small M6/M7 budgets (about 10 min)
python run_all.py --only m3  # one or more modules, e.g. --only m4 m5
```

One script, `run_all.py`, runs every module in order and writes all tables to `results/` and
all figures to `figures/`. The reported numbers come from the full run; `--fast` checks that the
chain runs but its M6/M7 numbers are not the reported ones. Most of the full run is M6 (about
40 min, 8,500 simulated configurations), M7 (about 7.5 min, 75,000 simulated days), M2 (about
3 min) and M5 (about 1.5 min). Running M2 re-runs M1 with the optimised plate mass, so M1's files
in `results/` are post-M2.

Optional extras, not needed by `run_all.py`:

- `python src/m7_decision/memo_pdf.py` rebuilds `report/decision_memo.pdf` (needs pandoc and Chrome/Chromium).
- `SKYLINK_M2_MESH=fine python run_all.py --only m1 m2` runs M2 on a 1 mm mesh instead of 2 mm (about 30 min).

On Google Colab: `!git clone https://github.com/id24d001/Skylink.git`, `%cd Skylink`,
`!pip install -q -r requirements.txt`, `!python run_all.py --fast`.

## Module map

| Module | Code | What it decides | Verification (in `results/`, `figures/`) |
|---|---|---|---|
| M0 Problem framing | `src/m0_framing/` | objectives, constraints with numbers, decision hierarchy; data audit | `m0_decision_hierarchy.png`, `m0_data_checks.csv` |
| M1 Vehicle MDO | `src/m1_vehicle/` | five vehicle designs A–E (radius 10–24 km) by MDF sizing, snapped to the catalogue | `m1_mda_convergence.png`, `m1_gradient_check.png`, `m1_mdf_vs_idf.csv` |
| M2 Hub plate topology | `src/m2_plate/` | SIMP plate-bending topology: 108.7 g cut plate, 2.31× solid compliance, ≥ 6 mm members | `m2_mbb_benchmark.png`, `m2_verification.csv`, `m2_plate_outline.dxf` |
| M3 Rotor surrogate | `src/m3_surrogate/` | cubic-RBF FM(D, T) from 30 oracle runs; M1 re-run with it | `m3_final_error_table.csv`, `m3_loo_parity_final.png`, `m1_fm_rerun.png` |
| M4 Hub network and fleet | `src/m4_network/` | MILP (HiGHS) hub siting, assignment, drones and bays per design | `m4_bruteforce.csv`, `m4_lp_gap.csv`, `m4_networks.png` |
| M5 Discrete-event simulator | `src/m5_simulation/` | SimPy day simulator with hourly wind, outages, queues and road fallback | `m5_mmc_check.png`, `m5_days_ci.csv`, `m5_results.png`, `m5_live_day.gif` |
| M6 Multi-objective optimisation | `src/m6_moo/` | NSGA-II (common random numbers) over fleet, spares, bays and reserve; knee | `m6_zdt1_check.csv`, `m6_ws_slice.png`, `m6_resim.png`, `m6_pareto.png` |
| M7 Decision and defense | `src/m7_decision/` | robustness, reliability Monte Carlo, sensitivity, minimax regret, final choice | `m7_robustness.png`, `m7_reliability.png`, `m7_sensitivity.png`, `m7_regret.csv` |

Each module is `src/<module>/run.py` with `run(ctx)`; it receives the outputs of earlier modules in
`ctx` and returns its own (for example M3 passes `ctx["fm_surrogate"]` to the M1 re-run and M4).

## Repository layout

```
run_all.py               one script for the whole chain
requirements.txt
data/                    given data pack, unchanged (components, costs, demand, hourly_profile,
│                        nofly, sites, wind_days)
└── oracle/              FM oracle requests and results (request_2/3, result_1/2/3)
src/
├── common/              repo paths and data loaders shared by all modules
└── m0_framing/ … m7_decision/
notebooks/               M1 and M2 working notebooks; M3 exploration and the oracle-request trail
                         (request 1 LHS, step 3/4 fit, request 2, refit, request 3, final)
results/  figures/       tables and figures written by run_all.py (plus the M3 notebook figures
                         cited in the appendix)
report/                  written deliverables (below)
```

## Deliverables

| Deliverable | Where |
|---|---|
| Code that runs end to end from one script | `run_all.py` (this README) |
| Formulation cards M0–M7, one page each | `report/formulation_cards/M0.md` … `M7.md` |
| Verification appendix, one figure or table per module | `report/verification_appendix.md` |
| Decision log, with AI suggestions accepted or rejected | `report/decision_log.md` |
| Two-page decision memo for the DHO (M7) | `report/decision_memo.md`, `report/decision_memo.pdf` |
| 3-minute Pareto pitch | `report/pareto_pitch.md` |
| M3 oracle requests and their defence | `report/m3_request2_defence.md`, `report/m3_request3_defence.md` |

The cards, appendix sections and decision-log entries are also submitted as eight module-wise PDFs.
