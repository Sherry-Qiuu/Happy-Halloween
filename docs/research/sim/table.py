# -*- coding: utf-8 -*-
import csv, json, os
from collections import defaultdict

base = os.path.dirname(os.path.abspath(__file__))
rows = list(csv.DictReader(open(os.path.join(base, "out", "persona_summary.csv"), encoding="utf-8-sig")))
agg = {(r["persona"], r["policy"]): r for r in rows}
personas = []
for r in rows:
    if r["persona"] not in personas:
        personas.append(r["persona"])
names = {
    "freshman": "大一新生", "kaoyan": "考研党", "club": "社团负责人",
    "intern": "实习+上课", "adhd": "高启动摩擦", "perfection": "完美主义",
    "timezone": "跨时区远程", "caregiver": "照护者", "athlete": "运动员",
    "freelance": "自由职业", "lowdata": "低记录型", "examweek": "考试周",
}
print("| 人群 | P0 | P2 | P4 | P4-P0 | 冲突 P0→P4 | 叠加/月 |")
print("| --- | --- | --- | --- | --- | --- | --- |")
for p in personas:
    a = float(agg[(p, "P0")]["goal_attainment"])
    b = float(agg[(p, "P2")]["goal_attainment"])
    c = float(agg[(p, "P4")]["goal_attainment"])
    d = (c - a) * 100
    co0 = float(agg[(p, "P0")]["conflict_minutes"])
    co4 = float(agg[(p, "P4")]["conflict_minutes"])
    st = float(agg[(p, "P4")]["stacks"])
    print(f"| {names[p]} | {a:.2f} | {b:.2f} | {c:.2f} | {d:+.1f}pp | {co0:.0f}→{co4:.0f} | {st:.1f} |")

best = defaultdict(int)
for p in personas:
    vals = {pol: float(agg[(p, pol)]["goal_attainment"]) for pol in ("P0", "P1", "P2", "P3", "P4")}
    best[max(vals, key=vals.get)] += 1
print("best policy count:", dict(best))
