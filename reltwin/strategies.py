"""Maintenance strategies compared by renewal-reward Monte Carlo, machine by machine.

One cycle = from installation to the next renewal (failure repair or planned replacement).
Cost rate = E[cost per cycle] / E[cycle length]; this is the long-run cost per operating hour.

- run_to_failure:   repair when it breaks
- time_based(T):    planned replacement at age T, repair if it breaks before
- route(I):         portable vibration measurement every I hours; a defect is detectable during the P-F
                    interval before failure, caught with probability p per measurement; the job is then
                    planned and done after a lead time (if the machine has not failed by then)
- online:           permanent sensor, same logic with near-continuous measurement
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.integrate import quad

from .weibull import reliability

PLANNED_LOSS_FACTOR = 0.3   # share of the production loss rate incurred during a planned stop


@dataclass
class Machine:
    tag: str
    cls: str
    loss_keur_h: float
    safety: int
    environment: int


def machines(cfg) -> list[Machine]:
    return [Machine(*row) for row in cfg["fleet"]]


def _costs(c, m: Machine):
    fail = c["corrective_cost_keur"] + m.loss_keur_h * c["corrective_downtime_h"]
    plan = c["preventive_cost_keur"] + PLANNED_LOSS_FACTOR * m.loss_keur_h * c["preventive_downtime_h"]
    return fail, plan


def simulate(cfg: dict, m: Machine, strategy: str, param: float | None = None, n: int = 20000,
             seed: int = 0, beta: float | None = None, eta: float | None = None) -> dict:
    """Long-run cost rate (kEUR/year), failures/year and availability for one machine and one strategy.

    beta/eta override the class values (to plan with fitted parameters instead of the truth).
    """
    c = cfg["classes"][m.cls]
    mon = cfg["monitoring"]
    rng = np.random.default_rng(seed)
    b, e = beta or c["weibull_beta"], eta or c["weibull_eta_h"]
    life = e * rng.weibull(b, n)
    pf = np.minimum(rng.lognormal(np.log(c["pf_interval_h"][0]), c["pf_interval_h"][1], n), life)
    fail_cost, plan_cost = _costs(c, m)
    dc, dp = c["corrective_downtime_h"], c["preventive_downtime_h"]
    lead = mon["planning_lead_h"]
    insp_cost = np.zeros(n)
    fixed_rate = 0.0

    if strategy == "run_to_failure":
        planned = np.zeros(n, bool)
        end = life
    elif strategy == "time_based":
        planned = life > param
        end = np.where(planned, param, life)
    elif strategy in ("route", "online"):
        step = param if strategy == "route" else 1.0
        p = mon["route_detection"] if strategy == "route" else mon["online_detection"]
        start = life - pf                                  # defect becomes detectable
        first = np.ceil(start / step) * step               # first measurement inside the window
        latest = life - lead                               # detection after this is too late to plan
        k = np.floor(np.maximum(latest - first, -1) / step) + 1  # number of useful measurements
        k = np.clip(k, 0, None)
        # index of the first successful measurement: geometric(p), must be <= k
        g = rng.geometric(p, n)
        caught = g <= k
        t_detect = first + (g - 1) * step
        planned = caught
        end = np.where(caught, t_detect + lead, life)
        if strategy == "route":
            insp_cost = np.floor(end / step) * mon["route_cost_keur"]
        else:
            fixed_rate = mon["online_cost_keur_per_year"]
    else:
        raise ValueError(strategy)

    cost = np.where(planned, plan_cost, fail_cost) + insp_cost
    length = end + np.where(planned, dp, dc)
    hpy = cfg["hours_per_year"]
    rate = cost.mean() / length.mean() * hpy + fixed_rate
    return {"cost_keur_year": float(rate), "failures_per_year": float((~planned).mean() / length.mean() * hpy),
            "availability": float(end.mean() / length.mean())}


def age_replacement_rate(T, beta, eta, cf, cp, hpy):
    """Classical analytic cost rate of age replacement (no downtime), to validate the simulation."""
    R = reliability(T, beta, eta)
    mean_cycle = quad(lambda t: reliability(t, beta, eta), 0, T)[0]
    return (cp * R + cf * (1 - R)) / mean_cycle * hpy


ROUTE_INTERVALS = [168, 336, 720, 1440, 2160]
TBM_INTERVALS = [4000, 6000, 8000, 12000, 16000, 24000, 32000]


def best_options(cfg, m: Machine, seed=0, beta=None, eta=None) -> list[dict]:
    """Every strategy and setting for one machine, sorted by cost."""
    opts = [{"strategy": "run_to_failure", "param": None,
             **simulate(cfg, m, "run_to_failure", seed=seed, beta=beta, eta=eta)}]
    for T in TBM_INTERVALS:
        opts.append({"strategy": "time_based", "param": T,
                     **simulate(cfg, m, "time_based", T, seed=seed, beta=beta, eta=eta)})
    for I in ROUTE_INTERVALS:
        opts.append({"strategy": "route", "param": I, **simulate(cfg, m, "route", I, seed=seed, beta=beta, eta=eta)})
    opts.append({"strategy": "online", "param": None, **simulate(cfg, m, "online", seed=seed, beta=beta, eta=eta)})
    return sorted(opts, key=lambda o: o["cost_keur_year"])
