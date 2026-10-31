# -*- coding: utf-8 -*-
"""Compare the same persona policy with and without breathing blocks.

Usage: python breathing_eval.py
"""

from __future__ import annotations

import json
import statistics

from core import PERSONAS, Planner, ensure_breathing, persona_for
from evaluate import BASE, SCENARIOS, build_schedule, disruptions, score_for_user


def run(events, dis, persona, label):
    rows = []
    planner = Planner(persona, base_date=BASE)
    for d in dis:
        p = planner.propose(events, d)
        user_p, user_exp = score_for_user(p.plan, persona)
        rows.append({
            "goal": p.plan.goal,
            "changes": p.plan.changes,
            "social": p.plan.social,
            "conflict": p.plan.conflict,
            "slack": p.bill.get("slack_minutes", 0),
            "breathing": p.bill.get("breathing_minutes", 0),
            "load": p.bill.get("load_pct", 0),
            "breathing_loss": p.bill.get("breathing_loss", 0),
            "p_accept": user_p,
            "expected": user_exp,
            "infeasible": 1 if p.infeasible else 0,
            "latency": p.latency_ms,
        })
    return rows


def main():
    all_rows = {"plain": [], "breathing": []}
    per = {}
    for s in SCENARIOS:
        base_events = build_schedule(s)
        persona = persona_for(s.label)
        dis = disruptions(base_events, 60)
        variants = {
            "plain": base_events,
            "breathing": ensure_breathing(base_events, persona),
        }
        per[s.key] = {}
        for name, events in variants.items():
            rows = run(events, dis, persona, s.label)
            all_rows[name].extend(rows)
            per[s.key][name] = rows

    print(f"{'variant':<12}{'goal':>7}{'chg':>6}{'social':>8}{'conf':>7}"
          f"{'slack':>8}{'breath':>8}{'load':>7}{'loss':>7}{'p_acc':>8}{'lat':>7}")
    for name in ("plain", "breathing"):
        rows = all_rows[name]
        print(f"{name:<12}{statistics.mean(r['goal'] for r in rows):>7.2f}"
              f"{statistics.mean(r['changes'] for r in rows):>6.2f}"
              f"{statistics.mean(r['social'] for r in rows):>8.2f}"
              f"{statistics.mean(r['conflict'] for r in rows):>7.2f}"
              f"{statistics.mean(r['slack'] for r in rows):>8.0f}"
              f"{statistics.mean(r['breathing'] for r in rows):>8.0f}"
              f"{statistics.mean(r['load'] for r in rows):>7.0f}"
              f"{statistics.mean(r['breathing_loss'] for r in rows):>7.1f}"
              f"{statistics.mean(r['p_accept'] for r in rows):>8.2f}"
              f"{statistics.mean(r['latency'] for r in rows):>7.1f}")

    with open("out_breathing.json", "w", encoding="utf-8") as f:
        json.dump({
            name: {
                "n": len(rows),
                "goal": statistics.mean(r["goal"] for r in rows),
                "changes": statistics.mean(r["changes"] for r in rows),
                "social": statistics.mean(r["social"] for r in rows),
                "conflict": statistics.mean(r["conflict"] for r in rows),
                "slack_minutes": statistics.mean(r["slack"] for r in rows),
                "breathing_minutes": statistics.mean(r["breathing"] for r in rows),
                "load_pct": statistics.mean(r["load"] for r in rows),
                "breathing_loss": statistics.mean(r["breathing_loss"] for r in rows),
                "p_accept": statistics.mean(r["p_accept"] for r in rows),
                "latency_ms": statistics.mean(r["latency"] for r in rows),
            } for name, rows in all_rows.items()
        }, f, ensure_ascii=False, indent=2)
    print("\nwrote out_breathing.json")


if __name__ == "__main__":
    main()
