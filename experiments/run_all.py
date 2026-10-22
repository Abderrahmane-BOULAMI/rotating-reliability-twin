"""Reproduce every number of the README (about 1 minute).   python experiments/run_all.py"""
import copy
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from reltwin.plan import export_excel, fleet_plan, totals  # noqa: E402
from reltwin.strategies import TBM_INTERVALS, age_replacement_rate, best_options, machines, simulate  # noqa: E402
from reltwin.weibull import b_life, bootstrap_b10, generate_history, load_config, mle, naive_fit  # noqa: E402

RES, FIG = ROOT / "results", ROOT / "results" / "figures"
INK, MUTED, A, B, C = "#1d2a33", "#8a99a6", "#e07b39", "#2f7f6f", "#30638e"


def style(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)


def main():
    t0 = time.time()
    FIG.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    out = {}

    # 1. life analysis from 8 years of history under the current practice
    hist = generate_history(cfg, years=8, policy_interval_h=cfg["current_practice"]["interval_h"], seed=cfg["seed"])
    hist.to_csv(RES / "history.csv", index=False)
    wrows, fitted, naive = [], {}, {}
    for cls, g in hist.groupby("class"):
        c = cfg["classes"][cls]
        bm, em = mle(g.age_h, g.failed)
        bn, en = naive_fit(g.age_h, g.failed)
        lo, hi = bootstrap_b10(g.age_h.to_numpy(), g.failed.to_numpy())
        fitted[cls], naive[cls] = (bm, em), (bn, en)
        wrows.append({"class": cls, "lives": len(g), "failures": int(g.failed.sum()),
                      "beta_true": c["weibull_beta"], "eta_true_h": c["weibull_eta_h"],
                      "beta_mle": bm, "eta_mle_h": em, "beta_naive": bn, "eta_naive_h": en,
                      "b10_true_h": b_life(0.1, c["weibull_beta"], c["weibull_eta_h"]),
                      "b10_mle_h": b_life(0.1, bm, em), "b10_naive_h": b_life(0.1, bn, en),
                      "b10_mle_90ci_h": f"{lo:.0f} to {hi:.0f}"})
    weib = pd.DataFrame(wrows)
    weib.to_csv(RES / "weibull.csv", index=False)
    out["weibull"] = weib.round(2).to_dict("records")

    # 2. validation of the simulation against the analytic age-replacement model
    cls = cfg["classes"]["pump"]
    val = []
    m0 = copy.deepcopy(machines(cfg)[0])
    c0 = copy.deepcopy(cfg)
    c0["classes"]["pump"]["corrective_downtime_h"] = 0
    c0["classes"]["pump"]["preventive_downtime_h"] = 0
    for T in [6000, 12000, 24000]:
        sim = simulate(c0, m0, "time_based", T, n=200000)["cost_keur_year"]
        ana = age_replacement_rate(T, cls["weibull_beta"], cls["weibull_eta_h"], cls["corrective_cost_keur"],
                                   cls["preventive_cost_keur"], cfg["hours_per_year"])
        val.append({"T_h": T, "simulated": sim, "analytic": ana, "error_pct": 100 * (sim / ana - 1)})
    out["validation"] = pd.DataFrame(val).round(3).to_dict("records")

    # 3. fleet plan: planned with the correct fit, with the naive fit, and the current practice
    plan = fleet_plan(cfg, fitted)
    plan_naive = fleet_plan(cfg, naive)
    plan_truth = fleet_plan(cfg, None)
    plan.to_csv(RES / "fleet_plan.csv", index=False)
    export_excel(plan, weib, RES / "maintenance_plan.xlsx")
    out["fleet"] = {"planned_with_censored_fit": totals(plan), "planned_with_naive_fit": totals(plan_naive),
                    "planned_with_true_parameters": totals(plan_truth)}
    out["top_critical"] = plan.head(6)[["tag", "class", "criticality", "strategy", "setting_h",
                                        "current_cost_keur_y", "planned_cost_keur_y"]].round(2).to_dict("records")

    # 4. sensitivity: what if defects give less warning (shorter P-F interval)?
    m = next(x for x in machines(cfg) if x.tag == "P-104")
    sens = []
    for scale in [0.1, 0.25, 0.5, 1.0, 2.0]:
        c2 = copy.deepcopy(cfg)
        c2["classes"]["pump"]["pf_interval_h"][0] *= scale
        best = best_options(c2, m)[0]
        tb = min((o for o in best_options(c2, m) if o["strategy"] == "time_based"), key=lambda o: o["cost_keur_year"])
        sens.append({"pf_median_h": c2["classes"]["pump"]["pf_interval_h"][0], "best": best["strategy"],
                     "setting_h": best["param"], "best_cost": best["cost_keur_year"],
                     "best_time_based_cost": tb["cost_keur_year"]})
    out["sensitivity_P104"] = pd.DataFrame(sens).round(2).to_dict("records")

    # figures
    fig, ax = plt.subplots(figsize=(6.5, 3))
    vals = [out["fleet"]["planned_with_censored_fit"]["current_cost_keur_y"],
            out["fleet"]["planned_with_naive_fit"]["planned_cost_keur_y"],
            out["fleet"]["planned_with_censored_fit"]["planned_cost_keur_y"],
            out["fleet"]["planned_with_true_parameters"]["planned_cost_keur_y"]]
    labels = ["Current practice\n(replace every 16 000 h)", "Plan from naive fit", "Plan from censored fit",
              "Plan with true\nparameters (bound)"]
    ax.barh(labels, vals, color=[MUTED, A, B, MUTED])
    for i, v in enumerate(vals):
        ax.text(v, i, f" {v:,.0f}", va="center", fontsize=8)
    ax.set_xlabel("Fleet maintenance + production-loss cost (kEUR/year)")
    style(ax)
    fig.tight_layout()
    fig.savefig(FIG / "fig1_fleet_cost.png", dpi=150)

    fig, ax = plt.subplots(figsize=(6.5, 3.2))
    pm = next(x for x in machines(cfg) if x.tag == "P-102")
    ys = [simulate(cfg, pm, "time_based", T)["cost_keur_year"] for T in TBM_INTERVALS]
    ax.plot(TBM_INTERVALS, ys, "o-", color=C, label="systematic replacement every T hours")
    for lab, strat, par, col in [("run to failure", "run_to_failure", None, A),
                                 ("monthly vibration route", "route", 720, B), ("online monitoring", "online", None, INK)]:
        ax.axhline(simulate(cfg, pm, strat, par)["cost_keur_year"], color=col, ls="--", lw=1, label=lab)
    ax.set_xlabel("replacement interval T (h)")
    ax.set_ylabel("kEUR/year")
    ax.set_title("Pump P-102 (4 kEUR/h of lost production)", fontsize=9)
    ax.legend(fontsize=7, frameon=False)
    style(ax)
    fig.tight_layout()
    fig.savefig(FIG / "fig2_pump_strategies.png", dpi=150)

    fig, ax = plt.subplots(figsize=(6.5, 3.2))
    g = hist[hist["class"] == "pump"]
    t = np.linspace(1, 60000, 300)
    cp = cfg["classes"]["pump"]
    for (b, e), lab, col, ls in [((cp["weibull_beta"], cp["weibull_eta_h"]), "true", INK, "-"),
                                 (fitted["pump"], "fit with censored lives", B, "-"),
                                 (naive["pump"], "fit on failures only", A, "--")]:
        ax.plot(t, 1 - np.exp(-(t / e) ** b), color=col, ls=ls, label=f"{lab} (beta {b:.2f}, eta {e:,.0f} h)")
    ax.set_xlabel("age (h)")
    ax.set_ylabel("probability of failure")
    ax.set_title(f"Pumps: {int(g.failed.sum())} failures and {int((~g.failed).sum())} censored lives", fontsize=9)
    ax.legend(fontsize=7, frameon=False)
    style(ax)
    fig.tight_layout()
    fig.savefig(FIG / "fig3_weibull.png", dpi=150)

    out["runtime_s"] = round(time.time() - t0)
    (RES / "summary.json").write_text(json.dumps(out, indent=1, default=float))
    print(json.dumps(out, indent=1, default=float))


if __name__ == "__main__":
    main()
