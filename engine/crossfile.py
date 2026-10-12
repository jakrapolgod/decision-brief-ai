"""โจทย์ข้ามไฟล์: ส่วนเพิ่มที่ขายได้ คุ้มกับส่วนเพิ่มที่จ่ายซื้อวัตถุดิบหรือไม่ (เช่น ขายสินค้ารับรอง vs ซื้อวัตถุดิบรับรอง)

ใช้เมื่อแนบ 2 ชุดข้อมูลที่ต่างมี "กลุ่มพรีเมียม" (ชื่อมีคำว่า รับรอง/certified/organic/premium/…) กับกลุ่มทั่วไป
- ฝั่งซื้อ = ชุดที่ราคาต่อหน่วยต่ำกว่า (วัตถุดิบ) · ฝั่งขาย = ชุดที่ราคาสูงกว่า (สินค้า)
- ส่วนเพิ่มซื้อ = ยอดจ่ายกลุ่มพรีเมียม − ปริมาณ × ราคากลุ่มทั่วไป "วันเดียวกัน จุดรับซื้อเดียวกัน"
- ส่วนเพิ่มขาย = ราคาขายกลุ่มพรีเมียม − ราคากลุ่มทั่วไป "วันใกล้ที่สุดภายใน ±3 วัน" (ถ่วงน้ำหนักตามปริมาณ)
- ได้ = ส่วนเพิ่มขายเฉลี่ยของปี × ปริมาณที่ขายเป็นพรีเมียม · สุทธิ = ได้ − จ่าย
- อัตราแปลง (เช่น % ผลผลิตต่อวัตถุดิบ) ใช้แปลงวัตถุดิบพรีเมียมเป็นสินค้าพรีเมียมที่ "ควรขายได้"
"""
from __future__ import annotations

import os
import re

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from .analysis import AMBER, BAD, BLUE, GREY, OK, propose, result, style
from .data_io import SENSITIVE_VALUE, Profile, profile_steps, read_many_steps, run_steps

CERT = re.compile(r"certif|รับรอง|organic|ออร์แกนิก|premium|พรีเมียม|fair\s*trade|rspo|fsc|msc", re.I)
TOL_DAYS = 3
TYPE = "premium_roi"


# ============================================================ หาโครงของแต่ละฝั่ง
def group_labels(p: Profile, group: str, qty: str) -> dict:
    """ชื่ออ่านง่ายของแต่ละกลุ่ม เช่น รหัส 30 → 'วัตถุดิบ Organic' · ชีต B → 'สินค้า Organic'
    ใช้คอลัมน์ข้อความที่ค่าที่พบบ่อยสุดในแต่ละกลุ่มไม่ซ้ำกัน และไม่ใช่ชื่อคน/บริษัท"""
    d = p.df[p.df[qty].notna()]
    vals = list(d[group].dropna().unique())
    best = None
    for c in p.cols.values():
        if c.name == group or c.sensitive or c.kind not in ("category", "text") or c.name.startswith("_"):
            continue
        modes, cover = {}, []
        for v in vals:
            s = d.loc[d[group] == v, c.name].dropna().astype(str).str.strip()
            if s.empty:
                break
            m = s.mode().iloc[0]
            modes[v] = m
            cover.append((s == m).mean())
        else:
            if len(set(modes.values())) == len(vals) and min(cover) >= .6 \
                    and not any(SENSITIVE_VALUE.search(m) for m in modes.values()):
                # คอลัมน์ที่เป็นคำอธิบาย (มีตัวอักษร ยาวกว่า) ดีกว่าคอลัมน์รหัส เมื่อครอบคลุมเท่ากัน
                labels = list(modes.values())
                sc = (round(float(np.mean(cover)), 2), np.mean([bool(re.search(r"[A-Za-zก-๙]", m)) for m in labels]),
                      np.mean([len(m) for m in labels]))
                if best is None or sc > best[0]:
                    best = (sc, modes)
    return {v: (best[1][v] if best else str(v)) for v in vals}


def _qty_matching_amount(p: Profile, qty: str, amount: str) -> str:
    """ปริมาณที่ใช้คู่กับยอดเงินต้องเป็นฐานเดียวกัน: ถ้ามีคอลัมน์ราคาต่อหน่วย เลือกคอลัมน์ปริมาณที่ ยอดเงิน ≈ ปริมาณ × ราคา
    (เช่น ใบแจ้งหนี้คิดเงินจาก 'ปริมาณ' ไม่ใช่ 'ปริมาณสุทธิ' ที่หักแล้ว)"""
    prices = [c for c in p.numbers("price") if c != amount and not re.search(r"amount|total|ยอด|มูลค่า|รวม|value", c, re.I)]
    qtys = [c for c in p.flows("qty") if c != amount]
    if not prices or len(qtys) < 2:
        return qty
    d = p.df[p.df[amount].abs() > 0]
    best = None
    for qc in qtys:
        for pc in prices:
            ok = d[[qc, pc]].notna().all(axis=1)
            if ok.mean() < .5:
                continue
            x = d[ok]
            hit = ((x[amount] - x[qc] * x[pc]).abs() <= x[amount].abs() * .001 + 1).mean()
            if best is None or hit > best[0] + .001 or (abs(hit - best[0]) <= .001 and qc == qty):
                best = (hit, qc)
    return best[1] if best and best[0] >= .9 else qty


def side_spec(p: Profile, gap: dict) -> dict | None:
    """จากโจทย์ gap ของชุดข้อมูล → กลุ่มทั่วไป (อ้างอิง) + กลุ่มพรีเมียม"""
    pr = gap["params"]
    g, q, a = pr["group"], _qty_matching_amount(p, pr["qty"], pr["amount"]), pr["amount"]
    d = p.df[(p.df[q] > 0) & p.df[a].notna()]
    lab = group_labels(p, g, q)
    vol = d.groupby(g)[q].sum().sort_values(ascending=False)
    prem = [v for v in vol.index if CERT.search(f"{v} {lab.get(v, '')}")]
    plain = [v for v in vol.index if v not in prem]
    if not prem or not plain:
        return None
    ref = plain[0]
    unit = d[d[g] == ref][a].sum() / d[d[g] == ref][q].sum()
    return dict(group=g, qty=q, amount=a, date=pr["date"], site=pr.get("site"), ref=ref, prem=prem,
                labels={str(k): v for k, v in lab.items()}, unit_price=float(unit))


def _short(labels: list[str]) -> str:
    """ชื่อรวมของหลายกลุ่ม: ส่วนต้นที่เหมือนกัน (เช่น 'วัตถุดิบOrganic/A' + 'วัตถุดิบOrganic/B' → 'วัตถุดิบOrganic')"""
    if len(labels) == 1:
        return labels[0]
    pre = os.path.commonprefix(labels).rstrip(" /-_(")
    return pre if len(pre) >= 4 else " + ".join(labels)


def conversion_default(profiles: list[Profile], cands_by_ds: list[list[dict]], buy_i: int, sell_i: int, years: list[int]) -> tuple[float, str]:
    """อัตราแปลงวัตถุดิบ → สินค้า: ใช้ % จากไฟล์ผลผลิตถ้าแนบมา (Σตัวตั้ง ÷ Σตัวหาร ช่วงปีเดียวกัน)
    ไม่งั้นประมาณจาก ปริมาณขายทั้งหมด ÷ ปริมาณรับซื้อทั้งหมด"""
    for i, (p, cands) in enumerate(zip(profiles, cands_by_ds)):
        if i in (buy_i, sell_i) or not p.date_col:
            continue
        for c in cands:
            if c["type"] != "ratio":
                continue
            num, den = c["params"]["num"], c["params"]["den"]
            # ใช้ปีล่าสุดที่ครบ 12 เดือนในช่วงเดียวกัน = ประสิทธิภาพโรงงานปัจจุบัน (ไม่ใช่ค่าเฉลี่ยหลายปี)
            d = p.df[p.df[p.date_col].dt.year.isin(years)]
            months = d.groupby(d[p.date_col].dt.year)[p.date_col].apply(lambda s: s.dt.month.nunique())
            full = [int(y) for y, m in months.items() if m == 12]
            if full:
                d = d[d[p.date_col].dt.year == full[-1]]
            if d[den].sum() > 0:
                r = d[num].sum() / d[den].sum() * 100
                if 5 <= r <= 60:
                    span = f"ปี {full[-1]}" if full else f"ปี {years[0]}–{years[-1]}"
                    return round(r, 1), f"{num} ÷ {den} จากไฟล์ {p.name} {span}"
    return np.nan, ""


def _kw(labels: list[str], fallback: str) -> str:
    m = [CERT.search(x) for x in labels]
    m = [x.group(0).upper() for x in m if x]
    return m[0] if m else fallback


def cross_candidates(profiles: list[Profile], cands_by_ds: list[list[dict]]) -> list[dict]:
    specs = []
    for i, (p, cands) in enumerate(zip(profiles, cands_by_ds)):
        gap = next((c for c in cands if c["type"] == "gap"), None)
        sp = side_spec(p, gap) if gap else None
        if sp:
            specs.append((i, sp))
    out = []
    for a in range(len(specs)):
        for b in range(a + 1, len(specs)):
            (i, si), (j, sj) = specs[a], specs[b]
            (bi, buy), (si_, sell) = ((i, si), (j, sj)) if si["unit_price"] < sj["unit_price"] else ((j, sj), (i, si))
            pb, ps = profiles[bi], profiles[si_]
            yb = set(pb.df[buy["date"]].dt.year.dropna().astype(int))
            ys = set(ps.df[sell["date"]].dt.year.dropna().astype(int))
            years = sorted(yb & ys)
            if not years:
                continue
            bn = _short([buy["labels"][str(v)] for v in buy["prem"]])
            sn = _short([sell["labels"][str(v)] for v in sell["prem"]])
            conv, conv_src = conversion_default(profiles, cands_by_ds, bi, si_, years)
            kb = _kw([buy["labels"][str(v)] for v in buy["prem"]], bn)
            ks = _kw([sell["labels"][str(v)] for v in sell["prem"]], sn)
            out.append(dict(
                type=TYPE, ds=[bi, si_], short_title=f"ความคุ้มค่า {kb}: ส่วนเพิ่มขาย {ks} vs ส่วนเพิ่มซื้อ",
                params=dict(buy=buy, sell=sell, conv=conv, conv_src=conv_src, target=90),
                title=f"ส่วนเพิ่มที่ขาย {sn} ได้ คุ้มกับส่วนเพิ่มที่จ่ายซื้อ {bn} หรือไม่",
                question=f"ส่วนเพิ่มที่ขาย {sn} ได้ คุ้มกับส่วนเพิ่มที่จ่ายซื้อ {bn} หรือไม่ — ควรจ่ายระดับนี้ต่อหรือปรับ?",
                why=f"จับคู่ไฟล์ซื้อ ({pb.name}) กับไฟล์ขาย ({ps.name}) · ปี {years[0]}–{years[-1]}",
                score=95))
    return out


# ============================================================ วิเคราะห์
def _buy_premium(p: Profile, sp: dict) -> pd.DataFrame:
    g, q, a, date, site = sp["group"], sp["qty"], sp["amount"], sp["date"], sp["site"]
    d = p.df[(p.df[q] > 0) & p.df[a].notna() & p.df[date].notna()]
    d = d.assign(_d=d[date].dt.normalize(), _s=d[site].astype(str) if site else "")
    ref = d[d[g] == sp["ref"]].groupby(["_d", "_s"]).agg(rw=(q, "sum"), ra=(a, "sum"))
    ref["rp"] = ref.ra / ref.rw
    r = d[d[g].isin(sp["prem"])].join(ref["rp"], on=["_d", "_s"]).dropna(subset=["rp"])
    return pd.DataFrame({"date": r["_d"], "grp": r[g].astype(str), "w": r[q], "paid": r[a] - r[q] * r["rp"]})


def _sell_premium(p: Profile, sp: dict) -> pd.DataFrame:
    g, q, a, date = sp["group"], sp["qty"], sp["amount"], sp["date"]
    d = p.df[(p.df[q] > 0) & p.df[a].notna() & p.df[date].notna()]
    day = d.assign(_d=d[date].dt.normalize()).groupby(["_d", g], as_index=False).agg(kg=(q, "sum"), baht=(a, "sum"))
    ref = day[day[g] == sp["ref"]].set_index("_d").eval("baht / kg").sort_index().rename("rp").reset_index()
    rs = day[day[g].isin(sp["prem"])].sort_values("_d").reset_index(drop=True)
    near = pd.merge_asof(rs[["_d"]], ref, on="_d", direction="nearest", tolerance=pd.Timedelta(days=TOL_DAYS))
    rs["prem"] = rs.baht / rs.kg - near["rp"].values
    return pd.DataFrame({"date": rs["_d"], "grp": rs[g].astype(str), "kg": rs.kg, "prem": rs.prem})


def a_premium(pb: Profile, ps: Profile, buy: dict, sell: dict, conv: float = np.nan, conv_src: str = "", target: int = 90) -> dict:
    B, Sx = _buy_premium(pb, buy), _sell_premium(ps, sell)
    end = min(pb.df[buy["date"]].max(), ps.df[sell["date"]].max()).normalize()
    start = max(pb.df[buy["date"]].min(), ps.df[sell["date"]].min()).normalize()
    B, Sx = B[B.date <= end], Sx[Sx.date <= end]
    if not np.isfinite(conv):                       # ไม่มีไฟล์ผลผลิต → ประมาณจากปริมาณขาย ÷ ปริมาณซื้อ
        bq = pb.df[(pb.df[buy["date"]] >= start) & (pb.df[buy["date"]] <= end)][buy["qty"]].clip(lower=0).sum()
        sq = ps.df[(ps.df[sell["date"]] >= start) & (ps.df[sell["date"]] <= end)][sell["qty"]].clip(lower=0).sum()
        conv = round(sq / bq * 100, 1) if bq else 20.0
        conv_src = "ประมาณจาก ปริมาณขายทั้งหมด ÷ ปริมาณรับซื้อทั้งหมด (แนบไฟล์ผลผลิตเพื่อใช้ % จริง)"
    years = sorted(set(B.date.dt.year) & set(Sx.date.dt.year))
    if not years:
        raise ValueError("ช่วงเวลาของไฟล์ซื้อและไฟล์ขายไม่ทับกัน")
    partial = {y for y in years if (y == end.year and end.month < 12) or (y == start.year and start.month > 1)}
    ylab = lambda y: f"{y}" + (" *" if y in partial else "")
    lb, ls = buy["labels"], sell["labels"]
    bn = _short([lb[str(v)] for v in buy["prem"]])
    sn = _short([ls[str(v)] for v in sell["prem"]])
    rn = ls.get(str(sell["ref"]), str(sell["ref"]))

    rows = []
    for y in years:
        by, sy = B[B.date.dt.year == y], Sx[Sx.date.dt.year == y]
        m = sy.dropna(subset=["prem"])
        prem_kg = (m.prem * m.kg).sum() / m.kg.sum() if m.kg.sum() else np.nan
        sold_t = sy.kg.sum() / 1000
        paid = by.paid.sum()
        earned = prem_kg * sold_t * 1000
        cert_t = by.w.sum() / 1000 * conv / 100
        per = {ls.get(gk, gk): (gm.prem * gm.kg).sum() / gm.kg.sum() for gk, gm in m.groupby("grp") if gm.kg.sum()}
        rows.append(dict(year=y, paid=paid, earned=earned, net=earned - paid, prem_kg=prem_kg, buy_kg=paid / by.w.sum(),
                         sold_t=sold_t, cert_t=cert_t, share=sold_t / cert_t if cert_t else np.nan,
                         match=m.kg.sum() / sy.kg.sum() if sy.kg.sum() else np.nan, raw_t=by.w.sum() / 1000, per=per))
    Y = pd.DataFrame(rows).set_index("year")
    Y["breakeven"] = Y.paid / (Y.sold_t * 1000)
    Y["lost"] = (Y.cert_t - Y.sold_t).clip(lower=0) * 1000 * Y.prem_kg

    full = [y for y in years if y not in partial]
    fy = full[-1] if full else years[-1]              # ปีหลักของ KPI = ปีล่าสุดที่ครบ 12 เดือน
    c, last = Y.loc[fy], Y.iloc[-1]
    ly = Y.index[-1]
    best = Y.net.idxmax()
    shrinking = last.net < Y.net.max() and ly != best

    # รายไตรมาส (ไตรมาสที่จบแล้ว และมีทั้งซื้อและขาย)
    qs = Sx.dropna(subset=["prem"]).groupby(Sx.date.dt.to_period("Q")).apply(lambda x: (x.prem * x.kg).sum() / x.kg.sum(), include_groups=False)
    qb = B.groupby(B.date.dt.to_period("Q")).apply(lambda x: x.paid.sum() / x.w.sum(), include_groups=False)
    Q = pd.DataFrame({"sell": qs * conv / 100, "buy": qb}).dropna(how="all")
    Qc = Q[[pq.end_time.normalize() <= end for pq in Q.index]].dropna()

    xl = [ylab(y) for y in Y.index]
    f1 = go.Figure()
    f1.add_bar(x=xl, y=Y.earned / 1e6, name=f"ได้จากขาย {sn}", marker_color=OK, text=[f"{v / 1e6:,.1f}" for v in Y.earned], textposition="outside")
    f1.add_bar(x=xl, y=Y.paid / 1e6, name=f"จ่ายซื้อ {bn}", marker_color=BAD, text=[f"{v / 1e6:,.1f}" for v in Y.paid], textposition="outside")
    f1.add_scatter(x=xl, y=Y.net / 1e6, name="สุทธิ", mode="lines+markers+text", text=[f"{v / 1e6:+,.1f}" for v in Y.net],
                   textposition="top center", line=dict(color=BLUE, width=3))
    f1.update_layout(barmode="group")
    f1.update_yaxes(title="ล้านบาท")
    ql = [str(x) for x in Q.index]
    f2 = go.Figure()
    f2.add_scatter(x=ql, y=Q.sell, name="ได้จากขาย (ต่อ หน่วยวัตถุดิบ)", mode="lines+markers", line=dict(color=OK, width=3), connectgaps=False)
    f2.add_scatter(x=ql, y=Q.buy, name="จ่ายซื้อ", mode="lines+markers", line=dict(color=BAD, width=3))
    f2.update_yaxes(title="บาท/กก. วัตถุดิบ", rangemode="tozero")
    f3 = go.Figure()
    f3.add_bar(x=xl, y=Y.cert_t, name=f"สินค้าจาก {bn} (ประมาณ)", marker_color=GREY, text=[f"{v:,.0f}" for v in Y.cert_t], textposition="outside")
    f3.add_bar(x=xl, y=Y.sold_t, name=f"ขายเป็น {sn}", marker_color=OK, text=[f"{v:,.0f} ({s:.0%})" for v, s in zip(Y.sold_t, Y.share)], textposition="outside")
    f3.update_layout(barmode="group")
    f3.update_yaxes(title="ตัน")
    for f in (f1, f2, f3):
        f.update_xaxes(type="category")
    xs = np.linspace(40, 100, 61)
    nets = (c.prem_kg * c.cert_t * 1000 * xs / 100 - c.paid) / 1e6
    be_share = c.paid / (c.prem_kg * c.cert_t * 1000) * 100 if c.prem_kg > 0 else np.nan
    net_t = (c.prem_kg * c.cert_t * 1000 * target / 100 - c.paid)
    f4 = go.Figure()
    f4.add_scatter(x=xs, y=nets, mode="lines", line=dict(color=BLUE, width=3), name="สุทธิ")
    f4.add_hline(y=0, line=dict(color="#56657E", width=1))
    f4.add_scatter(x=[c.share * 100], y=[c.net / 1e6], mode="markers+text", text=["ตอนนี้"], textposition="top left", marker=dict(size=13, color=AMBER), name="ตอนนี้")
    f4.add_vline(x=target, line=dict(color=OK, dash="dot"), annotation_text=f"เป้า {target}%")
    f4.update_xaxes(title=f"% ที่ขายเป็น {sn}")
    f4.update_yaxes(title="ล้านบาท")

    q_last = (f"{Qc.index[-1]} ขายได้ {Qc.sell.iloc[-1]:.2f} vs จ่าย {Qc.buy.iloc[-1]:.2f} บาท/กก. "
              f"(ช่วงดีสุด {Qc.sell.max():.2f} vs {Qc.buy.loc[Qc.sell.idxmax()]:.2f} ใน {Qc.sell.idxmax()})") if len(Qc) else "ยังไม่มีไตรมาสที่ครบ"
    verdict = "คุ้ม" if c.net > 0 else "ไม่คุ้ม"
    tail = (f" · ปี {ylab(ly)} (ถึง {end:%d/%m/%Y}) สุทธิ {last.net / 1e6:+,.1f} ล้าน — กำไรหดลง "
            f"ส่วนเพิ่มขาย {last.prem_kg:.2f} vs จุดคุ้มทุน {last.breakeven:.2f} บาท/กก.") if ly != fy else ""
    per_txt = " · ".join(f"{k} {v:.2f}" for k, v in c.per.items())
    tab = Y[["raw_t", "cert_t", "sold_t", "share", "buy_kg", "prem_kg", "breakeven", "paid", "earned", "net", "lost"]].copy()
    for col in ["paid", "earned", "net", "lost"]:
        tab[col] = tab[col] / 1e6
    tab["share"] = tab["share"] * 100
    tab.index = xl
    tab.columns = [f"{bn} (ตัน)", "สินค้าที่ควรขายได้ (ตัน)", f"ขาย {sn} (ตัน)", "ขายเป็นพรีเมียม (%)", "ส่วนเพิ่มซื้อ (บาท/กก.)",
                   "ส่วนเพิ่มขาย (บาท/กก.)", "จุดคุ้มทุน (บาท/กก.)", "จ่าย (ล้าน)", "ได้ (ล้าน)", "สุทธิ (ล้าน)", "เสียส่วนเพิ่ม (ล้าน)"]

    res = result(
        answer=f"{verdict} — ปี {ylab(fy)} สุทธิ {c.net / 1e6:+,.1f} ล้านบาท (ได้ {c.earned / 1e6:,.1f} − จ่าย {c.paid / 1e6:,.1f}){tail}",
        kpis=[(f"ผลสุทธิปี {ylab(fy)} (ได้จากขาย − จ่ายซื้อ)", f"{c.net / 1e6:+,.1f} ล้าน",
               "ปีที่ดีที่สุด" if fy == best else f"{(c.net - Y.net.max()) / 1e6:+,.1f} ล้าน เทียบปี {best}"),
              (f"ส่วนเพิ่มขาย {sn} ปี {ylab(fy)} (บาท/กก.)", f"{c.prem_kg:.2f}", f"{c.prem_kg - c.breakeven:+.2f} เทียบจุดคุ้มทุน {c.breakeven:.2f}"),
              (f"ขายเป็น {sn} ได้ ปี {ylab(fy)}", f"{c.share:.0%}", f"{(c.share - target / 100) * 100:+.0f} pp เทียบเป้า {target}%")],
        charts=[dict(title=f"ปี {best} ได้สุทธิ {Y.net.max() / 1e6:+,.1f} ล้าน แต่ปี {ylab(ly)} เหลือ {last.net / 1e6:+,.1f} ล้าน"
                     + (" — จ่ายเพิ่มเร็วกว่าที่ขายได้" if shrinking else ""),
                     sub=f"ส่วนเพิ่มที่ได้จากขาย {sn} vs ที่จ่ายซื้อ {bn} (ล้านบาท) · * = ไม่ครบปี", fig=style(f1)),
                dict(title=f"ส่วนต่างรายไตรมาส — {q_last}",
                     sub=f"ส่วนเพิ่มต่อ กก. วัตถุดิบ · ขาย = ส่วนเพิ่มขาย × {conv:.1f}% · ช่องว่าง = ไตรมาสที่ไม่มีขายพรีเมียม", fig=style(f2)),
                dict(title=f"ขายเป็น {sn} ได้ {c.share:.0%} (ปี {ylab(fy)}) — ส่วนที่เหลือขายเป็น{rn} เสียส่วนเพิ่ม ~{c.lost / 1e6:,.1f} ล้าน",
                     sub=f"ตัน: สินค้าที่ผลิตจาก {bn} (ประมาณ) vs ขายเป็น {sn} จริง", fig=style(f3)),
                dict(title=f"ถ้าขายเป็น {sn} ได้ {target}% ปี {ylab(fy)} จะได้สุทธิ {net_t / 1e6:+,.1f} ล้าน · ต้องขายได้ ≥ {be_share:.0f}% จึงคุ้มทุน",
                     sub=f"ผลสุทธิ (ล้านบาท) ตาม % ที่ขายเป็นพรีเมียม · ที่ส่วนเพิ่มขาย {c.prem_kg:.2f} บาท/กก.", fig=style(f4, legend=False))],
        insight=(f"{'ยังคุ้ม แต่กำไรหดลง' if c.net > 0 and shrinking else verdict} — ส่วนเพิ่มที่จ่ายซื้อ{'เพิ่ม' if Y.buy_kg.iloc[-1] > Y.buy_kg.iloc[0] else 'ลด'}จาก "
                 f"{Y.buy_kg.iloc[0]:.3f} เป็น {Y.buy_kg.iloc[-1]:.3f} บาท/กก. ขณะที่ส่วนเพิ่มที่ขายได้"
                 f"{'ไม่ได้เพิ่มตาม' if Y.prem_kg.iloc[-1] / max(Y.prem_kg.iloc[0], 1e-9) < Y.buy_kg.iloc[-1] / max(Y.buy_kg.iloc[0], 1e-9) else 'เพิ่มตาม'}"
                 f"{' และขายเป็นพรีเมียมได้ไม่หมด' if c.share < .95 else ''}"),
        hypothesis=f"ส่วนเพิ่มซื้อที่สูงขึ้นอาจมาจากการแข่งซื้อ {bn} ไม่ใช่จากราคาขาย {sn} ที่สูงขึ้น · ปี {ylab(fy)} ส่วนเพิ่มขายแยกกลุ่ม: {per_txt}",
        do=(f"ผูกส่วนเพิ่มที่จ่ายกับส่วนเพิ่มที่ขายได้ (ส่วนเพิ่มซื้อ ≤ ส่วนเพิ่มขาย × {conv:.1f}% × สัดส่วนที่ขายเป็นพรีเมียม) "
            f"และเพิ่มสัดส่วนที่ขายเป็น {sn} เป็น ≥ {target}% ก่อนรับซื้อ {bn} เพิ่ม"),
        not_do=f"ไม่ขึ้นส่วนเพิ่มซื้อตามคู่แข่งโดยไม่มีสัญญาขาย {sn} รองรับ",
        tradeoff=f"ลดส่วนเพิ่มซื้ออาจเสียผู้ขาย {bn} ให้โรงงานอื่น — ใช้สัญญาขายล่วงหน้าเป็นตัวกำหนดปริมาณที่ควรรับซื้อ",
        actions=[("ประชุมฝ่ายขาย + ฝ่ายรับซื้อทุกเดือน: ตั้งส่วนเพิ่มซื้อจากส่วนเพิ่มขายของเดือนนั้น", "CFO + ผู้จัดการฝ่ายขาย + ฝ่ายรับซื้อ", "เริ่มเดือนหน้า", "ผลสุทธิ ≥ 0 ทุกไตรมาส"),
                 (f"หาสัญญาขาย {sn} เพิ่มให้พอกับสินค้าที่ผลิตจาก {bn}", "ผู้จัดการฝ่ายขาย", "1 ไตรมาส", f"ขายเป็นพรีเมียม {c.share:.0%} → ≥ {target}%"),
                 (f"รับซื้อ {bn} ตามปริมาณที่มีสัญญาขายรองรับ · ส่วนที่เกินจ่ายราคาทั่วไป", "ผู้จัดการฝ่ายรับซื้อ", "ไตรมาสถัดไป", f"ส่วนเพิ่มขาย ≥ จุดคุ้มทุน {c.breakeven:.2f} บาท/กก."),
                 ("ทบทวนรายไตรมาสด้วย dashboard นี้ · ถ้าสุทธิติดลบ 2 ไตรมาสติด ให้ลดส่วนเพิ่มซื้อ", "ผู้บริหาร", "ทุกไตรมาส", "—")],
        limits=[f"ส่วนเพิ่มขายเทียบราคากลุ่มทั่วไป ({rn}) วันใกล้ที่สุดภายใน ±{TOL_DAYS} วัน — จับคู่ได้ {c.match:.0%} ของปริมาณปี {ylab(fy)} · ส่วนที่จับคู่ไม่ได้ใช้ส่วนเพิ่มเฉลี่ยของปี",
                "สัญญาขายพรีเมียมอาจตกลงราคาล่วงหน้า — วันที่ส่งของไม่ใช่วันที่ตกลงราคา ตัวเลขจึงเป็นค่าประมาณ",
                f"สินค้าจาก {bn} = ปริมาณ × {conv:.1f}% ({conv_src}) ไม่ได้วัดแยก",
                "ยังไม่รวมค่ารับรอง/ตรวจสอบ และส่วนเพิ่มของผลพลอยได้ — ผลสุทธิจริงอาจต่างจากนี้",
                f"ส่วนเพิ่มซื้อเทียบราคากลุ่ม '{lb.get(str(buy['ref']), buy['ref'])}' วันเดียวกัน" + (" จุดรับซื้อเดียวกัน" if buy["site"] else "")],
        table=tab.round(3),
        line_points=[f"ปี {ylab(fy)} {verdict} สุทธิ {c.net / 1e6:+,.1f} ล้าน (ได้ {c.earned / 1e6:,.1f} − จ่าย {c.paid / 1e6:,.1f})",
                     (f"ปี {ylab(ly)} เหลือ {last.net / 1e6:+,.1f} ล้าน ใกล้จุดคุ้มทุน" if ly != fy else f"ขายเป็นพรีเมียมได้ {c.share:.0%}")],
        line_reco=f"ผูกส่วนเพิ่มซื้อกับราคาขาย และขายเป็นพรีเมียม ≥ {target}%")
    if (Y.share > 1.05).any():
        res["limits"].insert(0, f"บางปีขายเป็นพรีเมียมมากกว่าที่ผลิตได้ (> 100%) — อัตราแปลง {conv:.1f}% อาจต่ำไป หรือมีซื้อสินค้าพรีเมียมจากภายนอกมาขาย ควรตรวจก่อนใช้สัดส่วนนี้")
    res["source_note"] = f"ไฟล์ซื้อ {pb.name} + ไฟล์ขาย {ps.name} · {start:%b %Y} – {end:%d %b %Y}"
    res["metrics"] = Y.drop(columns=["per"])           # ตัวเลขรายปีไว้ตรวจสอบ/ทดสอบ
    return res


# ============================================================ รวมโจทย์จากหลายไฟล์
def propose_all(profiles: list[Profile], k: int = 5) -> list[dict]:
    return run_steps(propose_all_steps(profiles, k))


def propose_all_steps(profiles: list[Profile], k: int = 5):
    """ไฟล์เดียว = เหมือนเดิม · หลายไฟล์ = โจทย์ข้ามไฟล์ก่อน แล้วเติมโจทย์เด่นของแต่ละไฟล์ (ระบุชื่อไฟล์)
    yield (สัดส่วน, ข้อความ) ทีละชุดข้อมูล — น้ำหนักตามจำนวนแถว · การจับคู่ข้ามไฟล์ราว 1 ใน 2 ของเวลาทั้งหมด"""
    if len(profiles) == 1:
        yield 0.0, f"คิดโจทย์จาก {profiles[0].name}"
        out = propose(profiles[0], k)
        for c in out:
            c["ds"] = 0
        return out
    rows = [max(p.n, 1) for p in profiles]
    per, done = [], 0
    for p, r in zip(profiles, rows):
        yield .5 * done / sum(rows), f"คิดโจทย์จาก {p.name}"
        per.append(propose(p, k=50))
        done += r
    yield .5, "จับคู่ไฟล์ซื้อกับไฟล์ขาย (โจทย์ข้ามไฟล์)"
    pool = cross_candidates(profiles, per)
    for i, (p, cands) in enumerate(zip(profiles, per)):
        for c in cands:
            c = dict(c, ds=i)
            c["title"] = f"[{p.name}] {c['title']}"
            pool.append(c)
    pool.sort(key=lambda c: -c["score"])
    out, cnt = [], {}
    for c in pool:
        t = c["type"]
        if cnt.get(t, 0) >= 2 or (t == "quality" and cnt.get(t)):
            continue
        out.append(c)
        cnt[t] = cnt.get(t, 0) + 1
        if len(out) == k:
            break
    tot = sum(c["score"] for c in out)
    for c in out:
        c["weight"] = round(c["score"] / tot * 100)
    if out:
        out[0]["weight"] += 100 - sum(c["weight"] for c in out)    # ปัดเศษแล้วให้รวมได้ 100 พอดี
    return out


def run_any(profiles: list[Profile], prob: dict) -> dict:
    from .analysis import run
    if prob["type"] != TYPE:
        return run(profiles[prob.get("ds", 0)], prob)
    try:
        res = a_premium(profiles[prob["ds"][0]], profiles[prob["ds"][1]], **prob["params"])
    except Exception as e:
        raise ValueError(f"วิเคราะห์โจทย์นี้ไม่ได้: {e}") from e
    res.update(title=prob["title"], question=prob.get("question", ""), type=TYPE, short_title=prob.get("short_title"))
    return res


# ============================================================ ประมวลผลไฟล์ที่แนบ พร้อมความคืบหน้า
def _scaled(gen, lo: float, hi: float, prefix: str = ""):
    """ส่งต่อความคืบหน้าของขั้นย่อย (0–1) ให้อยู่ในช่วง lo–hi ของทั้งงาน และคืนผลของขั้นย่อย"""
    while True:
        try:
            f, label = next(gen)
        except StopIteration as e:
            return e.value
        yield lo + (hi - lo) * min(max(f, 0.0), 1.0), prefix + label


def load_steps(files: list[tuple[str, bytes]]):
    """อ่านไฟล์ → จัดชนิดข้อมูล → คิดโจทย์ · yield (สัดส่วน 0–1 ของทั้งงาน, ข้อความ) · คืน (profiles, problems)
    สัดส่วนเวลาจากการวัดกับไฟล์ขนาดใหญ่ (หลายแสนแถว): อ่าน ~13% · จัดชนิด ~68% · คิดโจทย์ ~19%"""
    R, P = .13, .81
    sets = yield from _scaled(read_many_steps(files), 0.0, R)
    sizes = [max(len(df) * df.shape[1], 1) for _, df, _ in sets]
    profiles, done = [], 0
    for (name, df, notes), w in zip(sets, sizes):
        lo = R + (P - R) * done / sum(sizes)
        hi = R + (P - R) * (done + w) / sum(sizes)
        p = yield from _scaled(profile_steps(df, list(notes)), lo, hi, f"จัดชนิดข้อมูล {name}: ")
        p.name = name
        profiles.append(p)
        done += w
    problems = yield from _scaled(propose_all_steps(profiles), P, 1.0)
    yield 1.0, "เสร็จแล้ว"
    return profiles, problems
