# -*- coding: utf-8 -*-
import json, os, csv
from collections import defaultdict
import statistics

base = os.path.dirname(os.path.abspath(__file__))
runs = json.load(open(os.path.join(base, "out", "runs.json"), encoding="utf-8"))

by = defaultdict(list)
for r in runs:
    by[(r["persona"], r["policy"])].append(r)

personas = []
for (p, pol) in by:
    if p not in personas:
        personas.append(p)
policies = ["P0", "P1", "P2", "P3", "P4"]

metrics = ["goal_attainment", "changes_per_day", "prompts_per_day", "conflict_minutes",
           "notification_debt", "boundary_violations", "stacks", "recovery_days"]

print("goal attainment by persona x policy")
print(f"{'persona':<12}" + "".join(f"{p:>7}" for p in policies))
for p in personas:
    row = []
    for pol in policies:
        rs = by[(p, pol)]
        row.append(statistics.mean(r["goal_attainment"] for r in rs))
    print(f"{p:<12}" + "".join(f"{v:>7.3f}" for v in row))

print("\nconflict minutes by persona x policy")
print(f"{'persona':<12}" + "".join(f"{p:>8}" for p in policies))
for p in personas:
    row = []
    for pol in policies:
        rs = by[(p, pol)]
        row.append(statistics.mean(r["conflict_minutes"] for r in rs))
    print(f"{p:<12}" + "".join(f"{v:>8.0f}" for v in row))

print("\nP4 vs P0 delta (goal, conflict, changes, prompts)")
for p in personas:
    a = by[(p, "P0")]
    b = by[(p, "P4")]
    dg = statistics.mean(x["goal_attainment"] for x in b) - statistics.mean(x["goal_attainment"] for x in a)
    dc = statistics.mean(x["conflict_minutes"] for x in b) - statistics.mean(x["conflict_minutes"] for x in a)
    dch = statistics.mean(x["changes_per_day"] for x in b) - statistics.mean(x["changes_per_day"] for x in a)
    dp = statistics.mean(x["prompts_per_day"] for x in b) - statistics.mean(x["prompts_per_day"] for x in a)
    print(f"{p:<12} goal {dg:+.3f}  conflict {dc:+7.0f}  changes {dch:+.2f}  prompts {dp:+.2f}")

# CSV for persona x policy
out = os.path.join(base, "out", "persona_summary.csv")
with open(out, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["persona", "policy"] + metrics)
    for p in personas:
        for pol in policies:
            rs = by[(p, pol)]
            w.writerow([p, pol] + [round(statistics.mean(r[m] for r in rs), 3) for m in metrics])
print("\nwrote", out)
