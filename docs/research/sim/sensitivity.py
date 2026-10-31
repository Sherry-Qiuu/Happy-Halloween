# -*- coding: utf-8 -*-
"""Sensitivity: how the stacking credit (0.25 / 0.40 / 0.60) changes P4 outcomes."""
import statistics
import calendar_stress as cs

personas = cs.PERSONAS
policy = [p for p in cs.POLICIES if p.key == "P4"][0]

print(f"{'load':<6}{'goal':>8}{'conflict':>10}{'changes/d':>11}{'stacks':>8}{'notify':>8}")
rows_by_load = {}
for load in (0.25, 0.40, 0.60):
    cs.STACK_LOAD = load
    vals = []
    for persona in personas:
        for seed in range(8):
            sim = cs.Sim(persona, policy, seed * 777 + personas.index(persona))
            vals.append(sim.run())
    rows_by_load[load] = vals
    print(f"{load:<6}{statistics.mean(v['goal_attainment'] for v in vals):>8.3f}"
          f"{statistics.mean(v['conflict_minutes'] for v in vals):>10.0f}"
          f"{statistics.mean(v['changes_per_day'] for v in vals):>11.3f}"
          f"{statistics.mean(v['stacks'] for v in vals):>8.2f}"
          f"{statistics.mean(v['notification_debt'] for v in vals):>8.2f}")

print("\nper-persona goal at each load")
print(f"{'persona':<12}" + "".join(f"{l:>8}" for l in (0.25, 0.40, 0.60)))
for persona in personas:
    line = []
    for load in (0.25, 0.40, 0.60):
        vs = [v for v in rows_by_load[load] if v["persona"] == persona.key]
        line.append(statistics.mean(v["goal_attainment"] for v in vs))
    print(f"{persona.key:<12}" + "".join(f"{x:>8.3f}" for x in line))
