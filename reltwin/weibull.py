"""Weibull life analysis with right-censored data (machines removed before failing).

A preventive replacement or the end of the observation window is not a failure: the machine survived at
least that long. Dropping those records, as a quick spreadsheet analysis often does, makes the equipment
look less reliable than it is.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from scipy.optimize import minimize

DEFAULT = Path(__file__).resolve().parent.parent / "config" / "fleet.yaml"


def load_config(path=None) -> dict:
    with open(path or DEFAULT, encoding="utf-8") as f:
        return yaml.safe_load(f)


def reliability(t, beta, eta):
    return np.exp(-(np.asarray(t, float) / eta) ** beta)


def b_life(p, beta, eta):
    """Age at which a fraction p has failed (B10 for p = 0.10)."""
    return eta * (-np.log(1 - p)) ** (1 / beta)


def mle(times, failed) -> tuple[float, float]:
    """Maximum-likelihood (beta, eta) from ages at failure (failed=True) and at censoring (failed=False)."""
    t = np.asarray(times, float)
    f = np.asarray(failed, bool)
    if f.sum() < 2:
        raise ValueError("need at least two failures")

    def nll(p):
        beta, eta = np.exp(p)
        z = t / eta
        return -(np.sum(np.log(beta / eta) + (beta - 1) * np.log(z[f])) - np.sum(z ** beta))

    x0 = np.log([1.5, np.mean(t) * 1.5])
    res = minimize(nll, x0, method="Nelder-Mead", options={"xatol": 1e-6, "fatol": 1e-8, "maxiter": 4000})
    beta, eta = np.exp(res.x)
    return float(beta), float(eta)


def naive_fit(times, failed):
    """The common shortcut: fit on failures only, ignoring censored records."""
    t = np.asarray(times, float)[np.asarray(failed, bool)]
    return mle(t, np.ones(len(t), bool))


def bootstrap_b10(times, failed, n=200, seed=0) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    t, f = np.asarray(times), np.asarray(failed)
    vals = []
    for _ in range(n):
        i = rng.integers(0, len(t), len(t))
        if f[i].sum() >= 2:
            b, e = mle(t[i], f[i])
            vals.append(b_life(0.10, b, e))
    return tuple(np.percentile(vals, [5, 95]))


def generate_history(cfg: dict, years: float = 8.0, policy_interval_h: float | None = 16000, seed: int = 0):
    """Maintenance history of the fleet under the current practice: failures and preventive renewals.

    Each row is one life: age reached, and whether it ended by a failure (True) or not (preventive
    replacement or end of the observation window, False).
    """
    rng = np.random.default_rng(seed)
    horizon = years * cfg["hours_per_year"]
    rows = []
    for tag, cls, *_ in cfg["fleet"]:
        c = cfg["classes"][cls]
        t = 0.0
        while t < horizon:
            life = c["weibull_eta_h"] * rng.weibull(c["weibull_beta"])
            limit = min(policy_interval_h or np.inf, horizon - t)
            if life <= limit:
                rows.append([tag, cls, life, True])
                t += life + c["corrective_downtime_h"]
            else:
                rows.append([tag, cls, limit, False])
                t += limit + c["preventive_downtime_h"]
    return pd.DataFrame(rows, columns=["tag", "class", "age_h", "failed"])
