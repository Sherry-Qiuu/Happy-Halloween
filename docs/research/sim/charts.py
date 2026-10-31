# -*- coding: utf-8 -*-
import json, os, csv, math
from PIL import Image, ImageDraw, ImageFont

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "out")
FONT = r"C:\Windows\Fonts\msyh.ttc"
FONT_B = r"C:\Windows\Fonts\msyhbd.ttc"

BG = (246, 243, 236)
CARD = (255, 255, 255)
INK = (28, 36, 48)
MUTED = (107, 114, 128)
GRID = (229, 225, 216)
COLORS = {
    "P0": (154, 160, 166),
    "P1": (217, 164, 65),
    "P2": (47, 111, 107),
    "P3": (91, 124, 250),
    "P4": (217, 93, 57),
}
GREEN = (47, 111, 107)
RED = (192, 80, 77)
NAMES = {
    "freshman": "大一新生", "kaoyan": "考研党", "club": "社团负责人",
    "intern": "实习+上课", "adhd": "高启动摩擦", "perfection": "完美主义",
    "timezone": "跨时区远程", "caregiver": "照护者", "athlete": "运动员",
    "freelance": "自由职业", "lowdata": "低记录型", "examweek": "考试周",
}

def f(sz, bold=False):
    return ImageFont.truetype(FONT_B if bold else FONT, sz)

def text(d, xy, s, sz=18, color=INK, bold=False, anchor="la"):
    d.text(xy, s, font=f(sz, bold), fill=color, anchor=anchor)

def load():
    summary = json.load(open(os.path.join(OUT, "summary.json"), encoding="utf-8"))
    rows = list(csv.DictReader(open(os.path.join(OUT, "persona_summary.csv"), encoding="utf-8-sig")))
    return summary, rows

def fig1(summary):
    W, H = 1500, 1000
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    text(d, (60, 42), "五种日历策略的代价迁移", 42, INK, True)
    text(d, (60, 98), "32 seeds × 12 personas × 28 days。没有一代策略全面占优：收益与代价在六个指标之间搬家。", 20, MUTED)
    panels = [
        ("目标达成率", "goal_attainment", 1.0, "{:.2f}"),
        ("冲突分钟 / 月", "conflict_minutes", 650, "{:.0f}"),
        ("通知债 / 月", "notification_debt", 1.2, "{:.2f}"),
        ("边界越界 / 月", "boundary_violations", 2.0, "{:.1f}"),
        ("改动次数 / 天", "changes_per_day", 1.0, "{:.2f}"),
        ("确认次数 / 天", "prompts_per_day", 1.2, "{:.2f}"),
    ]
    pw, ph = 650, 245
    x0, y0 = 60, 150
    gap = 40
    for i, (title, key, vmax, fmt) in enumerate(panels):
        r, c = divmod(i, 2)
        x = x0 + c * (pw + gap)
        y = y0 + r * (ph + gap)
        d.rounded_rectangle([x, y, x + pw, y + ph], 16, fill=CARD, outline=GRID, width=1)
        text(d, (x + 24, y + 18), title, 22, INK, True)
        bx, by = x + 34, y + 62
        bw, bh = pw - 68, 140
        d.line([bx, by + bh, bx + bw, by + bh], fill=GRID, width=2)
        n = len(summary)
        slot = bw / n
        barw = slot * 0.56
        for j, a in enumerate(summary):
            v = a[key]
            h = 0 if vmax == 0 else max(2, int(v / vmax * (bh - 24)))
            cx = bx + j * slot + slot / 2
            d.rounded_rectangle([cx - barw / 2, by + bh - h, cx + barw / 2, by + bh],
                                4, fill=COLORS[a["policy"]])
            text(d, (cx, by + bh - h - 8), fmt.format(v), 15, INK, True, anchor="mb")
            text(d, (cx, by + bh + 8), a["policy"], 15, MUTED, False, anchor="ma")
    text(d, (60, H - 40), "注：目标达成率差异在噪声范围内；冲突、通知债、边界越界的差异更大。SD 见 summary.json。", 16, MUTED)
    img.save(os.path.join(OUT, "fig1_policy_tradeoffs.png"))

def fig2(rows):
    agg = {}
    for r in rows:
        agg[(r["persona"], r["policy"])] = float(r["goal_attainment"])
    personas = []
    for r in rows:
        if r["persona"] not in personas:
            personas.append(r["persona"])
    # order by delta
    deltas = [(p, (agg[(p, "P4")] - agg[(p, "P0")]) * 100) for p in personas]
    deltas.sort(key=lambda x: x[1])
    W, H = 1400, 900
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    text(d, (60, 42), "同一策略，不同的人：P4 相对 P0 的目标达成率变化", 38, INK, True)
    text(d, (60, 94), "ADHD、自由职业、运动员获益；大一新生、考试周、低记录型受损。单位：百分点。", 19, MUTED)
    left, right = 240, W - 120
    top, bottom = 160, H - 70
    vmin, vmax = -7, 6
    zero = left + (0 - vmin) / (vmax - vmin) * (right - left)
    for gv in (-6, -4, -2, 0, 2, 4, 6):
        gx = left + (gv - vmin) / (vmax - vmin) * (right - left)
        d.line([gx, top, gx, bottom], fill=GRID, width=1)
        text(d, (gx, bottom + 10), "0" if gv == 0 else f"{gv:+d}", 15, MUTED, False, anchor="ma")
    rowh = (bottom - top) / len(deltas)
    for i, (p, dv) in enumerate(deltas):
        cy = top + i * rowh + rowh / 2
        text(d, (left - 20, cy), NAMES[p], 18, INK, False, anchor="rm")
        x2 = left + (dv - vmin) / (vmax - vmin) * (right - left)
        color = GREEN if dv >= 0 else RED
        d.rounded_rectangle([min(zero, x2), cy - 14, max(zero, x2), cy + 14], 4, fill=color)
        tx = x2 + (8 if dv >= 0 else -8)
        text(d, (tx, cy), f"{dv:+.1f}", 16, color, True, anchor="lm" if dv >= 0 else "rm")
    d.line([zero, top, zero, bottom], fill=INK, width=2)
    text(d, (60, H - 36), "P4 = 硬度 + 最小代价 + 并行叠加 + 分级确认 + 通知草稿 + 诚实失败。", 16, MUTED)
    img.save(os.path.join(OUT, "fig2_persona_delta.png"))

def fig3(rows):
    agg = {}
    for r in rows:
        agg[(r["persona"], r["policy"])] = r
    personas = []
    for r in rows:
        if r["persona"] not in personas:
            personas.append(r["persona"])
    pts = []
    for p in personas:
        dg = (float(agg[(p, "P4")]["goal_attainment"]) - float(agg[(p, "P0")]["goal_attainment"])) * 100
        st = float(agg[(p, "P4")]["stacks"])
        pts.append((st, dg, p))

    W, H = 1200, 780
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    text(d, (60, 42), "并行叠加越多，目标损失越大——高启动摩擦者是例外", 34, INK, True)
    text(d, (60, 94), "横轴：P4 每月把任务挂到课堂/会议上的次数；纵轴：P4 相对 P0 的目标变化（百分点）。", 18, MUTED)
    left, right, top, bottom = 120, W - 70, 160, H - 90
    xmax, ymin, ymax = 3.5, -7, 6

    def sx(v): return left + v / xmax * (right - left)
    def sy(v): return bottom - (v - ymin) / (ymax - ymin) * (bottom - top)

    for gy in (-6, -4, -2, 0, 2, 4, 6):
        d.line([left, sy(gy), right, sy(gy)], fill=GRID, width=1)
        text(d, (left - 12, sy(gy)), f"{gy:+d}", 14, MUTED, False, anchor="rm")
    for gx in (0, 1, 2, 3):
        d.line([sx(gx), top, sx(gx), bottom], fill=GRID, width=1)
        text(d, (sx(gx), bottom + 10), str(gx), 14, MUTED, False, anchor="ma")
    d.line([left, sy(0), right, sy(0)], fill=INK, width=2)

    # least squares
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    den = sum((p[0] - mx) ** 2 for p in pts)
    slope = sum((p[0] - mx) * (p[1] - my) for p in pts) / den if den else 0
    intercept = my - slope * mx
    x1, x2 = 0, xmax
    d.line([sx(x1), sy(intercept + slope * x1), sx(x2), sy(intercept + slope * x2)],
           fill=(180, 160, 120), width=3)

    for x, y, name in pts:
        color = GREEN if y >= 0 else RED
        r = 9
        d.ellipse([sx(x) - r, sy(y) - r, sx(x) + r, sy(y) + r], fill=color, outline=CARD, width=2)
        text(d, (sx(x) + 12, sy(y) - 10), NAMES[name], 15, INK)
    # correlation
    sxv = math.sqrt(sum((p[0] - mx) ** 2 for p in pts))
    syv = math.sqrt(sum((p[1] - my) ** 2 for p in pts))
    rr = sum((p[0] - mx) * (p[1] - my) for p in pts) / (sxv * syv) if sxv and syv else 0
    text(d, (right - 10, top + 4), f"r = {rr:.2f}", 18, MUTED, True, anchor="ra")
    text(d, (60, H - 36), "解释：把 90 分钟专注挂到课上只记 40% 产出；对时间充裕的人是折价，对高启动摩擦的人是救回一块。", 15, MUTED)
    img.save(os.path.join(OUT, "fig3_stacking.png"))

def main():
    summary, rows = load()
    fig1(summary)
    fig2(rows)
    fig3(rows)
    print("charts written to", OUT)

if __name__ == "__main__":
    main()
