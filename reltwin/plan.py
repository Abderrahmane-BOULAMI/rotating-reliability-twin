"""Fleet maintenance plan: criticality, chosen strategy per machine, totals, Excel export."""
from __future__ import annotations

import pandas as pd

from .strategies import Machine, best_options, machines, simulate


def criticality(m: Machine, failures_per_year: float) -> dict:
    """Simple criticality: worst consequence (1..4) x frequency class (1..4), as in a risk matrix."""
    prod = 1 if m.loss_keur_h == 0 else 2 if m.loss_keur_h < 2 else 3 if m.loss_keur_h < 8 else 4
    consequence = max(prod, m.safety, m.environment)
    freq = 1 if failures_per_year < 0.05 else 2 if failures_per_year < 0.15 else 3 if failures_per_year < 0.3 else 4
    return {"consequence": consequence, "frequency": freq, "criticality": consequence * freq}


def fleet_plan(cfg, fitted: dict | None = None, seed: int = 0) -> pd.DataFrame:
    """Best strategy per machine. `fitted` maps class -> (beta, eta) used for PLANNING; the result is then
    evaluated with the class's true parameters (what would really happen)."""
    cur = cfg["current_practice"]
    rows = []
    for m in machines(cfg):
        b, e = (fitted or {}).get(m.cls, (None, None))
        best = best_options(cfg, m, seed=seed, beta=b, eta=e)[0]
        real = simulate(cfg, m, best["strategy"], best["param"], seed=seed + 1)
        now = simulate(cfg, m, cur["strategy"], cur["interval_h"], seed=seed + 1)
        rows.append({"tag": m.tag, "class": m.cls, "loss_keur_h": m.loss_keur_h,
                     **criticality(m, now["failures_per_year"]),
                     "current_cost_keur_y": now["cost_keur_year"], "current_failures_y": now["failures_per_year"],
                     "strategy": best["strategy"], "setting_h": best["param"],
                     "planned_cost_keur_y": real["cost_keur_year"], "planned_failures_y": real["failures_per_year"],
                     "availability": real["availability"]})
    return pd.DataFrame(rows).sort_values("criticality", ascending=False).reset_index(drop=True)


def totals(plan: pd.DataFrame) -> dict:
    return {"current_cost_keur_y": float(plan["current_cost_keur_y"].sum()),
            "planned_cost_keur_y": float(plan["planned_cost_keur_y"].sum()),
            "current_failures_y": float(plan["current_failures_y"].sum()),
            "planned_failures_y": float(plan["planned_failures_y"].sum()),
            "strategy_count": plan["strategy"].value_counts().to_dict()}


def export_excel(plan: pd.DataFrame, weibull: pd.DataFrame, path):
    labels = {"run_to_failure": "Corrective", "time_based": "Systematic replacement",
              "route": "Vibration route", "online": "Online monitoring"}
    out = plan.copy()
    out["strategy"] = out["strategy"].map(labels)
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        out.round(3).to_excel(xw, sheet_name="Maintenance plan", index=False)
        weibull.round(3).to_excel(xw, sheet_name="Weibull", index=False)
        for ws in xw.book.worksheets:
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = max(12, min(30, max(len(str(c.value)) for c in col)))
            ws.freeze_panes = "A2"
