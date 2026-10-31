# -*- coding: utf-8 -*-
import statistics
from evaluate import SCENARIOS, build_schedule, disruptions, run
from core import GENERIC, Planner, persona_for

print("| 画像 | 通用 → 画像 | 接受率 |")
print("| --- | --- | --- |")
for s in SCENARIOS:
    ev = build_schedule(s)
    dis = disruptions(ev, 80)
    user = persona_for(s.label)
    g = run(Planner(GENERIC), ev, dis, user)
    p = run(Planner(user), ev, dis, user)
    ge = statistics.mean(x["expected"] for x in g)
    pe = statistics.mean(x["expected"] for x in p)
    pa = statistics.mean(x["p_accept"] for x in p)
    print(f"| {s.key} | {ge:.3f} → {pe:.3f} | {pa:.2f} |")
