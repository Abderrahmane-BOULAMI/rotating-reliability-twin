import copy

import numpy as np
import pytest

from reltwin.plan import criticality, export_excel, fleet_plan, totals
from reltwin.strategies import Machine, age_replacement_rate, best_options, machines, simulate
from reltwin.weibull import b_life, generate_history, load_config, mle, naive_fit, reliability


@pytest.fixture(scope="module")
def cfg():
    return load_config()


def test_mle_recovers_parameters_with_censoring():
    rng = np.random.default_rng(1)
    life = 20000 * rng.weibull(2.0, 3000)
    cens = rng.uniform(5000, 30000, 3000)
    t, f = np.minimum(life, cens), life <= cens
    b, e = mle(t, f)
    assert b == pytest.approx(2.0, rel=0.06) and e == pytest.approx(20000, rel=0.04)
    bn, en = naive_fit(t, f)
    assert en < 0.8 * e          # dropping survivors makes the equipment look worse


def test_b_life_definition():
    b10 = b_life(0.10, 1.8, 26000)
    assert 1 - reliability(b10, 1.8, 26000) == pytest.approx(0.10)


def test_simulation_matches_analytic_age_replacement(cfg):
    c = copy.deepcopy(cfg)
    c["classes"]["pump"]["corrective_downtime_h"] = 0
    c["classes"]["pump"]["preventive_downtime_h"] = 0
    m = Machine("X", "pump", 0.0, 1, 1)
    p = c["classes"]["pump"]
    for T in (6000, 20000):
        sim = simulate(c, m, "time_based", T, n=200000)["cost_keur_year"]
        ana = age_replacement_rate(T, p["weibull_beta"], p["weibull_eta_h"], p["corrective_cost_keur"],
                                   p["preventive_cost_keur"], c["hours_per_year"])
        assert sim == pytest.approx(ana, rel=0.02)


def test_more_frequent_routes_catch_more_defects(cfg):
    m = Machine("X", "pump", 2.0, 2, 1)
    f_week = simulate(cfg, m, "route", 168)["failures_per_year"]
    f_quarter = simulate(cfg, m, "route", 2160)["failures_per_year"]
    f_rtf = simulate(cfg, m, "run_to_failure")["failures_per_year"]
    assert f_week < f_quarter < f_rtf


def test_production_loss_pushes_towards_monitoring(cfg):
    spared = best_options(cfg, Machine("A", "pump", 0.0, 1, 1))[0]["strategy"]
    critical = best_options(cfg, Machine("B", "pump", 10.0, 1, 1))[0]["strategy"]
    assert critical == "online" and spared != "online"


def test_history_ages_within_horizon(cfg):
    h = generate_history(cfg, years=2, policy_interval_h=16000, seed=0)
    assert (h["age_h"] > 0).all() and (h["age_h"] <= 16000 + 1e-6).all()
    assert set(h["tag"]) == {row[0] for row in cfg["fleet"]}


def test_criticality_matrix():
    m = Machine("K", "compressor", 15.0, 4, 3)
    assert criticality(m, 0.5) == {"consequence": 4, "frequency": 4, "criticality": 16}
    assert criticality(Machine("F", "fan", 0.0, 1, 1), 0.01)["criticality"] == 1


def test_plan_beats_current_practice_and_exports(cfg, tmp_path):
    plan = fleet_plan(cfg)
    t = totals(plan)
    assert t["planned_cost_keur_y"] < t["current_cost_keur_y"]
    assert len(plan) == len(machines(cfg))
    import pandas as pd
    export_excel(plan, pd.DataFrame({"a": [1]}), tmp_path / "plan.xlsx")
    assert (tmp_path / "plan.xlsx").stat().st_size > 1000
