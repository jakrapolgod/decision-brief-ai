"""เสนอโจทย์ + วิเคราะห์ตามหลักการวิเคราะห์ข้อมูลเพื่อการตัดสินใจ

หลักที่บังคับใช้ทุกโจทย์:
1. อัตราส่วนถ่วงน้ำหนักตามปริมาณเสมอ (Σตัวตั้ง ÷ Σตัวหาร) ไม่เฉลี่ย % รายเดือน
2. เทียบปีต้องตัดผลฤดูกาล: เทียบกับค่าปกติของเดือนเดียวกัน · ปีที่ไม่ครบ 12 เดือนติดป้าย *
3. เทียบราคาต้องเทียบวันเดียวกัน (และจุดเดียวกันถ้ามี) ไม่เทียบค่าเฉลี่ยทั้งปี
4. ความสัมพันธ์รายเดือนอาจหลอกตา — ดูความสัมพันธ์ภายในเดือนเดียวกันด้วย และบอกว่า "ยังไม่ใช่เหตุและผล"
5. ทุกหัวข้อกราฟเป็นข้อสรุป (insight) · ทุกผลมี ข้อเสนอ / ไม่ทำ / trade-off / แผนลงมือพร้อม KPI / ข้อจำกัด
6. ไม่มีชื่อคน บริษัท ทะเบียน หรือเลขบัญชีในผลลัพธ์ — ผู้ขาย/ลูกค้าแสดงเป็น "ราย 1, 2, …"
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from .data_io import TH_MONTHS, Profile, rx

OK, BAD, BLUE, GREY, AMBER, INK = "#2E9E57", "#D9534F", "#2F6FDE", "#9AA7B8", "#F2A516", "#1B2A4A"

TYPES = {
    "ratio": "อัตราส่วน/ประสิทธิภาพ (ถ่วงน้ำหนัก ตัดผลฤดูกาล)",
    "trend": "แนวโน้มรายปี + ฤดูกาล",
    "gap": "ส่วนต่างราคาระหว่างกลุ่ม (เทียบวันเดียวกัน)",
    "concentration": "การกระจุกตัวของลูกค้า/ผู้ขาย",
    "threshold": "ยอดคงเหลือต่ำกว่าเกณฑ์",
    "relation": "ความสัมพันธ์ระหว่าง 2 ตัวแปร",
    "quality": "คุณภาพและความครบถ้วนของข้อมูล",
    "premium_roi": "ความคุ้มค่าส่วนเพิ่ม ซื้อ vs ขาย (ข้ามไฟล์)",
}


# ============================================================ helpers
def fmt(v, d=None):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "—"
    a = abs(v)
    if d is None and float(v).is_integer() and a < 1e6:
        return f"{v:,.0f}"
    if d is None:
        d = 0 if a >= 100 else (1 if a >= 10 else 2)
    if a >= 1e6:
        return f"{v / 1e6:,.{max(d, 1) if a < 1e8 else 0}f} ล้าน"
    return f"{v:,.{d}f}"


def unit_of(col: str) -> str:
    """หน่วยจากชื่อคอลัมน์ เช่น 'ยอดผลิต (ตัน)' → 'ตัน'"""
    m = re.search(r"\(([^()]{1,8})\)\s*$", str(col))
    return m.group(1) if m else "หน่วย"


def style(fig, h=320, legend=True):
    fig.update_layout(height=h, margin=dict(l=8, r=8, t=36 if legend else 10, b=8), showlegend=legend,
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(family="IBM Plex Sans Thai, Sarabun, sans-serif", size=13, color=INK),
                      legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0, title_text=""))
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="#EEF1F6")
    return fig


def _ym(df, date):
    d = df[date]
    return d.dt.year, d.dt.month


def _full_years(df, date):
    y, m = _ym(df, date)
    c = pd.DataFrame({"y": y, "m": m}).dropna().groupby("y").m.nunique()
    return [int(k) for k, v in c.items() if v == 12]


def _ylab(y, full):
    return f"{int(y)}" + ("" if y in full else " *")


def result(**kw):
    base = dict(kpis=[], charts=[], limits=[], actions=[], table=None, line_points=[], hypothesis="", tradeoff="", not_do="")
    base.update(kw)
    return base


def _groupish(p: Profile):
    """คอลัมน์ที่ใช้แยกกลุ่มได้: ไม่อ่อนไหว ไม่ใช่เลขที่เอกสาร และค่าไม่ใช่แค่เครื่องหมาย (เช่น √)"""
    out = []
    for c in p.cols.values():
        if c.kind not in ("category", "text") or c.sensitive or not (3 <= c.nunique <= 300) or c.nonnull <= .5:
            continue
        if re.search(r"เลขที่|เลข|^no\.?$|(^|_)id$|invoice|doc|bill|time|เวลา", c.name, re.I):
            continue
        v = p.df[c.name].dropna().astype(str)
        if rx(v, r"[^\wก-๙]+", "fullmatch").mean() > .3:
            continue
        out.append(c)
    return out


# ============================================================ candidate problems
def propose(p: Profile, k: int = 5) -> list[dict]:
    df, date = p.df, p.date_col
    nums = p.flows()
    qty = [c for c in p.flows("qty")] or nums
    money = [c for c in p.flows("money") if not (p.cols[c].hints & {"price", "qty"})]
    daily = bool(date) and df[date].dt.normalize().nunique() >= 200
    per_month = (len(df) / max(1, df[date].dt.to_period("M").nunique())) if date else 0
    cands = []
    span_years = df[date].dt.year.nunique() if date else 0

    def total(c):
        return float(df[c].clip(lower=0).sum())

    def main_of(cols):                                     # ปริมาณหลัก: ใช้ค่าสุทธิ/เข้ากระบวนการก่อน ไม่ใช้น้ำหนักรวมรถ
        pref = [c for c in cols if re.search(r"net|สุทธิ|ผลิต|process", c, re.I) and not re.search(r"accum|สะสม", c, re.I)]
        return max(pref or cols, key=total) if cols else None

    qty = [c for c in qty if not re.search(r"(?:^|_)(?:in|out)$|weightin|weightout|ขาเข้า|ขาออก|gross|tare", c, re.I)] or qty

    # --- ratio: ตัวตั้ง/ตัวหาร ในหน่วยเดียวกัน (เช่น ผลผลิต/วัตถุดิบ) — ตัวหาร = คอลัมน์ปริมาณที่ใหญ่ที่สุด · ใช้เฉพาะคอลัมน์ปริมาณจริง
    rq = [c for c in qty if "qty" in p.cols[c].hints]
    if len(rq) >= 2:
        qty_all = qty
        qty = rq
        den = max(qty, key=total)
        alt = [c for c in qty if c != den and re.search(r"ผลิต|process|input|ใช้", c, re.I) and total(c) > .8 * total(den)]
        if alt:
            den = alt[0]                                   # ใช้ปริมาณที่เข้ากระบวนการจริง ถ้ามี
        for num in sorted([c for c in qty if c != den], key=total, reverse=True):
            x = df[[num, den]].dropna()
            x = x[(x[den] > 0) & (x[num] >= 0)]
            if len(x) < 10:
                continue
            r = (x[num] / x[den]).median()
            if not (0.003 < r < 0.9) or x[num].corr(x[den]) < .5:
                continue
            ratio = x[num].sum() / x[den].sum() * 100
            if date:                                           # สัดส่วนต้องเปลี่ยนตามเวลาจึงมีเรื่องให้ตัดสินใจ
                xm = df[[num, den, date]].dropna()
                mr = xm.groupby(xm[date].dt.to_period("M")).apply(lambda g: g[num].sum() / g[den].sum() * 100 if g[den].sum() else np.nan, include_groups=False)
                if mr.std() < .15:
                    continue
            spread = 0.0
            if date:
                yy = df.assign(y=df[date].dt.year).groupby("y").apply(lambda g: g[num].sum() / g[den].sum() * 100 if g[den].sum() else np.nan, include_groups=False)
                spread = float(yy.max() - yy.min()) if len(yy) > 1 else 0.0
            cands.append(dict(type="ratio", params=dict(num=num, den=den, date=date),
                              title=f"สัดส่วน {num} ต่อ {den} — ปีไหนดีที่สุด และเสียไปเท่าไหร่",
                              question=f"ปีหรือช่วงไหนได้ {num}/{den} ดีที่สุด (ตัดผลฤดูกาลแล้ว) และควรตั้งเป้าเท่าไหร่?",
                              why=f"เฉลี่ย {ratio:.2f}% · แกว่งระหว่างปี {spread:.2f} จุด",
                              score=62 + min(18, spread * 8) + (6 if date else 0)))
            if len([c for c in cands if c["type"] == "ratio"]) >= 2:
                break
        qty = qty_all

    # --- trend: ปริมาณ/มูลค่าหลักตามเวลา
    main = main_of(qty) or main_of(money)
    if date:
        if main:
            yt = df.groupby(df[date].dt.year)[main].sum()
            g = (yt.iloc[-1] / yt.iloc[-2] - 1) * 100 if len(yt) > 1 and yt.iloc[-2] else 0
            cands.append(dict(type="trend", params=dict(value=main, date=date, by=None),
                              title=f"แนวโน้ม {main} รายปีและฤดูกาล — ปีไหนสูงสุด เดือนไหนพีค",
                              question=f"{main} เปลี่ยนไปอย่างไรรายปี และเดือนไหนเป็นช่วงพีคที่ต้องเตรียมกำลังรองรับ?",
                              why=f"{span_years} ปี · ปีล่าสุดเปลี่ยน {g:+.0f}%", score=48 + min(20, span_years * 3)))
        else:
            gs = sorted(_groupish(p), key=lambda c: (not re.search(r"อาการ|ประเภท|อุปกรณ์|สาเหตุ|type|category|เครื่อง", c.name, re.I), c.name == "_sheet"))
            cat = gs[0].name if gs else None
            cands.append(dict(type="trend", params=dict(value=None, date=date, by=cat),
                              title="จำนวนเหตุการณ์ตามเวลา — เพิ่มขึ้นไหม และกระจุกที่อะไร",
                              question="จำนวนรายการเพิ่ม/ลดตามเวลาอย่างไร และกระจุกอยู่ที่กลุ่มไหน?",
                              why=f"{len(df):,} รายการ · {span_years} ปี", score=58))

    # --- gap: ราคาต่อหน่วยระหว่างกลุ่ม วันเดียวกัน
    amt = max(money, key=total) if money else None
    true_qty = [c for c in qty if c != amt and "qty" in p.cols[c].hints]    # ราคาต่อหน่วยต้องหารด้วยปริมาณจริง ไม่ใช่ยอดเงิน
    if date and amt and true_qty:
        q = main_of(true_qty)
        groups = [c for c in p.of("category") if not c.sensitive and 2 <= c.nunique <= 8 and c.nonnull > .5]
        best = None
        for g in groups:
            w = df.groupby(g.name)[q].sum().sort_values(ascending=False)
            if len(w) < 2 or w.iloc[1] / w.sum() < .03:
                continue
            # เทียบราคาได้เฉพาะวันที่กลุ่มหลักกับกลุ่มอื่นมีรายการพร้อมกัน — ไม่มีวันร่วมก็ไม่เสนอโจทย์นี้
            gd = df[[date, g.name]].dropna().assign(_d=lambda x: x[date].dt.normalize()).drop_duplicates([g.name, "_d"])
            ref_days = set(gd.loc[gd[g.name] == w.index[0], "_d"])
            if gd.loc[gd[g.name] != w.index[0], "_d"].isin(ref_days).sum() < 20:
                continue
            up = df.groupby(g.name).apply(lambda x: x[amt].sum() / x[q].sum() if x[q].sum() else np.nan, include_groups=False)
            diff = abs(up.iloc[0] - up.max()) / up.median() if up.median() else 0
            if not np.isfinite(diff):
                continue
            sc = 55 + min(20, diff * 200) + (15 if re.search(r"type|member|ประเภท|goods|สินค้า|_sheet|code", g.name, re.I) else 0)
            if best is None or sc > best[0]:
                best = (sc, g.name, w)
        if best:
            sc, g, w = best
            cands.append(dict(type="gap", params=dict(group=g, amount=amt, qty=q, date=date,
                                                      site=next((c.name for c in p.of("category") if re.search(r"site|สาขา|จุด|branch|location|warehouse|คลัง", c.name, re.I) and not c.sensitive), None)),
                              title=f"ราคาต่อหน่วยแต่ละกลุ่ม '{g}' ต่างกันเท่าไหร่ (เทียบวันเดียวกัน) และคุ้มไหม",
                              question=f"กลุ่มใน '{g}' ได้/จ่ายราคาต่างจากกลุ่มหลักเท่าไหร่ต่อหน่วย รวมเป็นเงินเท่าไหร่ต่อปี และควรปรับหรือไม่?",
                              why=f"{len(w)} กลุ่ม · กลุ่มหลัก {w.index[0]} {w.iloc[0] / w.sum():.0%}", score=sc))

    # --- concentration: ลูกค้า/ผู้ขาย
    ent = [c for c in p.cols.values() if c.kind in ("category", "text", "id") and 20 <= c.nunique <= max(25, .5 * p.n) and c.nonnull > .5
           and re.search(r"customer|ลูกค้า|ผู้ขาย|supplier|vendor|member|code|รหัส|name|ชื่อ", c.name, re.I)
           and not re.search(r"เลขที่|เลขใบ|invoice|doc|bill|weightno|running|goods|สินค้า|team|car", c.name, re.I)]
    if ent and (qty or money):
        e = max(ent, key=lambda c: (bool(re.search(r"code|รหัส", c.name, re.I)), c.nunique))
        v = main_of(qty) or amt
        s = df.groupby(e.name)[v].sum().sort_values(ascending=False)
        top20 = s.head(max(1, int(len(s) * .2))).sum() / s.sum()
        cands.append(dict(type="concentration", params=dict(entity=e.name, value=v, date=date),
                          title=f"{v} กระจุกที่รายใหญ่แค่ไหน และเสียรายไหนไปบ้าง",
                          question=f"ราย 20% แรกคิดเป็นกี่ % ของ {v} · รายที่หายไปมีปริมาณเท่าไหร่ · ควรมีโครงการรักษารายใหญ่หรือไม่?",
                          why=f"{len(s):,} ราย · 20% แรก = {top20:.0%}", score=45 + min(25, (top20 - .5) * 60)))

    # --- threshold: ยอดคงเหลือ
    bal = [c for c in p.numbers() if ("balance" in p.cols[c].hints or {"level", "money"} <= p.cols[c].hints) and "pct" not in p.cols[c].hints]
    if daily and bal:
        b = max(bal, key=lambda c: df[c].notna().sum())
        cands.append(dict(type="threshold", params=dict(value=b, date=date, threshold=None),
                          title=f"{b} ต่ำกว่าเกณฑ์กี่วัน — ควรตั้งขั้นต่ำเท่าไหร่",
                          question=f"{b} ต่ำกว่าระดับที่ปลอดภัยบ่อยแค่ไหน ควรตั้งขั้นต่ำและวงเงินสำรองเท่าไหร่?",
                          why=f"ข้อมูลรายวัน {df[date].dt.normalize().nunique():,} วัน", score=66))

    # --- relation: 2 ตัวแปรที่ไม่ใช่คู่อัตราส่วน
    if date and len(nums) >= 2 and per_month >= 5:
        pairs = []
        ratio_pairs = {(c["params"]["num"], c["params"]["den"]) for c in cands if c["type"] == "ratio"}
        mm = df.groupby(df[date].dt.to_period("M"))[nums[:12]].mean()
        for i, a in enumerate(nums[:12]):
            for b in nums[i + 1:12]:
                ha, hb = p.cols[a].hints, p.cols[b].hints
                trivial = bool(({"money", "qty"} & ha) and ({"money", "qty"} & hb))
                if (a, b) in ratio_pairs or (b, a) in ratio_pairs or a[:4] == b[:4] or trivial:
                    continue                                   # คู่อัตราส่วน หรือคอลัมน์ตระกูลเดียวกัน (เช่น ราคา / ราคาหักส่วนลด)
                r = mm[a].corr(mm[b])
                if pd.notna(r) and .2 < abs(r) < .95:
                    x = df[[a, b, date]].dropna()
                    gm = x.groupby(x[date].dt.to_period("M"))[[a, b]].transform("mean")
                    r_in = (x[a] - gm[a]).corr(x[b] - gm[b])
                    if pd.notna(r_in) and abs(r_in) >= .15:
                        pairs.append((abs(r_in), a, b, r))
        if pairs:
            _, a, b, r = max(pairs)
            cands.append(dict(type="relation", params=dict(x=a, y=b, date=date),
                              title=f"{a} สัมพันธ์กับ {b} จริงหรือไม่",
                              question=f"เมื่อ {a} เปลี่ยน {b} เปลี่ยนตามหรือไม่ (ดูทั้งรายเดือนและภายในเดือนเดียวกัน)?",
                              why=f"r รายเดือน = {r:+.2f}", score=40 + abs(r) * 15))

    # --- quality: เสมอ
    miss = 1 - np.mean([c.nonnull for c in p.cols.values()])
    cands.append(dict(type="quality", params=dict(date=date),
                      title="ข้อมูลพร้อมใช้ตั้งเป้าหรือยัง — ช่องว่าง ค่าซ้ำ เดือนที่หาย",
                      question="ข้อมูลชุดนี้ครบและถูกพอจะใช้ตั้ง KPI หรือไม่ ต้องแก้อะไรก่อน?",
                      why=f"ค่าว่างเฉลี่ย {miss:.0%}", score=30 + min(35, miss * 120)))

    # เติมตัวเลือกสำรองให้มีพอเลือก 5 โจทย์: แนวโน้มของค่าอื่น · จำนวนรายการแยกกลุ่ม · ยอดคงเหลือตัวอื่น
    if date:
        used = {c["params"].get("value") for c in cands if c["type"] in ("trend", "threshold")}
        for v in sorted([c for c in dict.fromkeys(qty + money) if c not in used], key=total, reverse=True)[:k]:
            cands.append(dict(type="trend", params=dict(value=v, date=date, by=None),
                              title=f"แนวโน้ม {v} รายปีและฤดูกาล — ปีไหนสูงสุด เดือนไหนพีค",
                              question=f"{v} เปลี่ยนไปอย่างไรรายปี และเดือนไหนเป็นช่วงพีค?", why=f"รวม {fmt(total(v))}", score=34))
        for c in sorted(_groupish(p), key=lambda c: (c.name == "_sheet", -c.nonnull))[:k]:
            cands.append(dict(type="trend", params=dict(value=None, date=date, by=c.name),
                              title=f"จำนวนรายการแยกตาม {c.name} — กลุ่มไหนมากที่สุด และเพิ่มขึ้นไหม",
                              question=f"รายการกระจุกที่ {c.name} ไหน และแนวโน้มรายปีเป็นอย่างไร?", why=f"{c.nunique} กลุ่ม", score=30))
        for v in ([c for c in bal if c not in used][:2] if daily else []):
            cands.append(dict(type="threshold", params=dict(value=v, date=date, threshold=None),
                              title=f"{v} ต่ำกว่าเกณฑ์กี่วัน — ควรตั้งขั้นต่ำเท่าไหร่",
                              question=f"{v} ต่ำกว่าระดับที่ปลอดภัยบ่อยแค่ไหน?", why="ยอดคงเหลือรายวัน", score=40))

    # เลือก 5 โจทย์ (ประเภทเดียวกันได้ไม่เกิน 2) แล้วแปลงคะแนนเป็นน้ำหนัก %
    cands.sort(key=lambda c: -c["score"])
    out, cnt = [], {}
    def same_as_chosen(c):                                # แนวโน้มที่แทบเหมือนตัวที่เลือกแล้ว (เช่น ยอดรวม vs ยอดเงินสุทธิ)
        v = c["params"].get("value")
        if c["type"] != "trend" or not v or not date:
            return False
        for o in out:
            w = o["params"].get("value")
            if o["type"] in ("trend", "threshold") and w and w != v:
                m = df.groupby(df[date].dt.to_period("M"))[[v, w]].sum()
                if m[v].corr(m[w]) > .98:
                    return True
        return False

    seen = set()
    for c in cands:
        c["title"] = c["title"].replace("'_sheet'", "ชีต").replace("_sheet", "ชีต")
        c["question"] = c["question"].replace("'_sheet'", "ชีต").replace("_sheet", "ชีต")
        c["key"] = (c["type"], tuple(sorted((k_, str(v)) for k_, v in c["params"].items())))
    for c in cands:
        if c["key"] in seen or cnt.get(c["type"], 0) >= 2 or same_as_chosen(c):
            continue
        seen.add(c["key"])
        out.append(c)
        cnt[c["type"]] = cnt.get(c["type"], 0) + 1
        if len(out) == k:
            break
    for c in cands:                                        # ยังไม่ครบ → ยอมให้ประเภทซ้ำเกิน 2
        if len(out) >= k:
            break
        if c["key"] not in seen and not same_as_chosen(c):
            seen.add(c["key"])
            out.append(c)
    tot = sum(c["score"] for c in out)
    for c in out:
        c["weight"] = round(c["score"] / tot * 100)
    return out


def guess_type(question: str) -> str:
    q = question.lower()
    rules = [("premium_roi", r"คุ้ม.*(ส่วนเพิ่ม|ขาย|ซื้อ)|ส่วนเพิ่ม.*(ขาย|ซื้อ).*(ขาย|ซื้อ)"), ("threshold", r"ต่ำกว่า|เกณฑ์|ขั้นต่ำ|เงินสด|คงเหลือ|balance"), ("gap", r"ส่วนเพิ่ม|ส่วนต่างราคา|ราคา.*(กลุ่ม|เทียบ)|premium"),
             ("concentration", r"ลูกค้า|ผู้ขาย|รายใหญ่|กระจุก|ราย 20"), ("ratio", r"สัดส่วน|อัตรา|%|เปอร์|ต่อ\s*\S+|yield|oer"),
             ("relation", r"สัมพันธ์|ผลต่อ|ส่งผล|เกี่ยวข้อง"), ("quality", r"คุณภาพข้อมูล|ข้อมูลครบ|ข้อมูลหาย|ความถูกต้อง")]
    for t, pat in rules:
        if re.search(pat, q):
            return t
    return "trend"


# ============================================================ analyses
def run(p: Profile, prob: dict) -> dict:
    fn = {"ratio": a_ratio, "trend": a_trend, "gap": a_gap, "concentration": a_conc,
          "threshold": a_threshold, "relation": a_relation, "quality": a_quality}[prob["type"]]
    try:
        res = fn(p, **prob["params"])
    except Exception as e:                                   # ข้อมูลไม่พอสำหรับโจทย์นี้ — แจ้งเหตุผลแทนการล้ม
        raise ValueError(f"วิเคราะห์โจทย์นี้ไม่ได้: {e}") from e
    res.setdefault("title", prob["title"])
    res["question"] = prob.get("question") or res.get("question", "")
    res["type"] = prob["type"]
    res["source_note"] = f"{p.n:,} แถว" + (f" · {p.df[p.date_col].min():%b %Y} – {p.df[p.date_col].max():%b %Y}" if p.date_col else "")
    return res


def _month_norm_ratio(d, num, den):
    g = d.groupby("m")[[num, den]].sum()
    return g[num] / g[den]


def a_ratio(p, num, den, date=None):
    d = p.df[[c for c in [num, den, date] if c]].dropna(subset=[num, den])
    d = d[d[den] > 0]
    if not date:
        r = d[num].sum() / d[den].sum() * 100
        return result(answer=f"{num}/{den} เฉลี่ย {r:.2f}%", kpis=[(f"{num}/{den}", f"{r:.2f}%", "")],
                      insight="ไม่มีคอลัมน์วันที่ จึงเทียบรายปีไม่ได้", do="เพิ่มวันที่ในข้อมูลเพื่อเทียบช่วงเวลา")
    d = d.dropna(subset=[date])
    d = d.assign(y=d[date].dt.year.astype(int), m=d[date].dt.month.astype(int))
    full = _full_years(d, date)
    norm = _month_norm_ratio(d, num, den)
    d["exp"] = d[den] * d.m.map(norm)
    Y = d.groupby("y").agg(num=(num, "sum"), den=(den, "sum"), exp=("exp", "sum"), n=("m", "nunique"))
    Y["r"] = Y.num / Y.den * 100
    Y["adj"] = (Y.num - Y.exp) / Y.den * 100
    F = Y[Y.index.isin(full)] if full else Y
    best, worst = F.r.idxmax(), F.r.idxmin()
    best_adj = F.adj.idxmax()
    last = F.index.max()
    L, B = Y.loc[last], Y.loc[best]
    gain = (B.r - L.r) / 100 * L.den
    seas = norm * 100
    lo_m, hi_m = int(seas.idxmin()), int(seas.idxmax())
    r_vol = d.groupby(d[date].dt.to_period("M")).apply(lambda g: pd.Series({"v": g[den].sum(), "r": g[num].sum() / g[den].sum()}), include_groups=False)
    rv = r_vol.v.corr(r_vol.r)
    xl = [_ylab(y, full) for y in Y.index]
    target = round(min(B.r, L.r + (B.r - L.r) * .5 + .0001), 1)
    unit = unit_of(num)

    f1 = go.Figure(go.Bar(x=xl, y=Y.r, marker_color=[OK if y == best else BAD if y == worst else (GREY if y not in full else BLUE) for y in Y.index],
                          text=[f"{v:.2f}" for v in Y.r], textposition="outside"))
    f1.add_hline(y=target, line=dict(color=AMBER, dash="dot"), annotation_text=f"เป้า {target}%")
    f1.update_yaxes(range=[max(0, Y.r.min() * .9), Y.r.max() * 1.06], title="%")
    f2 = go.Figure(go.Bar(x=xl, y=Y.adj, marker_color=[OK if v >= 0 else BAD for v in Y.adj], text=[f"{v:+.2f}" for v in Y.adj], textposition="outside"))
    f2.add_hline(y=0, line=dict(color="#56657E", width=1))
    f2.update_yaxes(title="จุด")
    f3 = go.Figure()
    f3.add_bar(x=xl, y=Y.den, name=den, marker_color=GREY)
    f3.add_scatter(x=xl, y=Y.r, name="%", yaxis="y2", mode="lines+markers", line=dict(color=BLUE, width=2))
    f3.update_layout(yaxis2=dict(overlaying="y", side="right", showgrid=False, tickformat=".1f"))
    f4 = go.Figure(go.Bar(x=[TH_MONTHS[m - 1] for m in seas.index], y=seas.values,
                          marker_color=[BAD if m == lo_m else OK if m == hi_m else GREY for m in seas.index],
                          text=[f"{v:.1f}" for v in seas.values], textposition="outside"))
    f4.update_yaxes(range=[seas.min() * .9, seas.max() * 1.05], title="%")
    for f in (f1, f2, f3, f4):
        f.update_xaxes(type="category")
    return result(
        answer=f"ปี {best} ได้ {num}/{den} ดีที่สุด {B.r:.2f}% · ปีล่าสุด ({last}) {L.r:.2f}% · ถ้าทำได้เท่าปี {best} ได้ {num} เพิ่ม ~{fmt(gain)} {unit}/ปี",
        kpis=[(f"{num}/{den} ปี {last}", f"{L.r:.2f}%", f"{L.r - B.r:+.2f} จุด เทียบปี {best}"),
              (f"ตัดผลฤดูกาลแล้ว ปี {best_adj} ดีที่สุด", f"{Y.adj.max():+.2f} จุด", f"ปี {last}: {L.adj:+.2f}"),
              (f"ได้เพิ่มถ้าทำได้เท่าปี {best}", f"{fmt(gain)}", f"{unit} ต่อปี ({num})")],
        charts=[dict(title=f"ปี {best} ได้สูงสุด {B.r:.2f}% · ปี {worst} ต่ำสุด {Y.loc[worst].r:.2f}%", sub=f"{num} ÷ {den} รายปี (ถ่วงน้ำหนัก) · * = ไม่ครบปี", fig=style(f1, legend=False)),
                dict(title=f"ตัดผลฤดูกาลแล้ว ปี {best_adj} ยังดีกว่าปกติมากที่สุด ({Y.adj.max():+.2f} จุด)", sub="เทียบค่าปกติของเดือนเดียวกันทุกปี · บวก = ดีกว่าปกติของฤดูนั้น", fig=style(f2, legend=False)),
                dict(title=f"ปริมาณ {den} กับ % แทบไม่สัมพันธ์ (r = {rv:.2f})" if abs(rv) < .3 else f"ปริมาณ {den} สัมพันธ์กับ % (r = {rv:.2f})", sub=f"แท่ง = {den} รายปี · เส้น = %", fig=style(f3)),
                dict(title=f"ฤดูกาล: ต่ำสุดเดือน {TH_MONTHS[lo_m - 1]} {seas[lo_m]:.1f}% · สูงสุด {TH_MONTHS[hi_m - 1]} {seas[hi_m]:.1f}%", sub="ค่าปกติของแต่ละเดือน (ทุกปีรวมกัน)", fig=style(f4, legend=False))],
        insight=f"ปี {best} ทำได้ดีที่สุดแม้ตัดผลฤดูกาลแล้ว — ปีล่าสุดห่างอยู่ {B.r - L.r:.2f} จุด · % ขึ้นกับฤดูกาล ({seas[hi_m] - seas[lo_m]:.1f} จุดระหว่างเดือน) มากกว่าปริมาณงาน",
        hypothesis=f"ปี {best} อาจได้วัตถุดิบดีกว่าหรือเครื่องจักรสภาพดีกว่า — ข้อมูลนี้ยังแยกสาเหตุไม่ได้",
        do=f"ตั้งเป้าปีหน้า {target}% (ครึ่งทางสู่ปีที่ดีที่สุด) และหาสาเหตุที่ปีล่าสุดต่ำกว่าเดือนเดียวกันของปี {best}",
        not_do=f"ไม่ตั้งเป้า {B.r:.2f}% ทันทีโดยยังไม่รู้สาเหตุ",
        tradeoff="การเพิ่ม % อาจต้องลดความเร็วหรือคัดวัตถุดิบเข้มขึ้น ซึ่งอาจลดปริมาณ",
        actions=[(f"หาสาเหตุที่ช่วง {TH_MONTHS[lo_m - 1]} ต่ำ — เทียบวัตถุดิบและการเดินเครื่อง", "ผู้จัดการฝ่ายผลิต", "30 วัน", f"% เดือน {TH_MONTHS[lo_m - 1]} {seas[lo_m]:.1f} → {seas[lo_m] + .5:.1f}"),
                 ("บันทึกข้อมูลที่ใช้แยกสาเหตุ (คุณภาพวัตถุดิบ การสูญเสียในกระบวนการ)", "ฝ่ายผลิต + QC", "เริ่มทันที", "บันทึกครบทุกวัน"),
                 ("ติดตามรายเดือนเทียบเดือนเดียวกันปีก่อน", "ผู้จัดการโรงงาน", "ทุกเดือน", f"ทั้งปี ≥ {target}%"),
                 ("ทบทวนเป้าหลังครบไตรมาส", "ผู้บริหาร", "ทุกไตรมาส", "—")],
        limits=["% ถ่วงน้ำหนักตามปริมาณ คำนวณจากตัวเลขในไฟล์ ไม่ใช้ % ที่พิมพ์ไว้", "ปีที่มี * มีข้อมูลไม่ครบ 12 เดือน ไม่ใช้จัดอันดับ"],
        table=Y.assign(**{"ปี": [_ylab(y, full) for y in Y.index]}).set_index("ปี")[["num", "den", "r", "adj"]].rename(
            columns={"num": num, "den": den, "r": "%", "adj": "เทียบเดือนเดียวกัน (จุด)"}).round(2),
        line_points=[f"ปี {best} ได้ดีที่สุด {B.r:.2f}% ปีล่าสุด {L.r:.2f}%", f"ถ้าทำได้เท่าปี {best} ได้เพิ่ม ~{fmt(gain)} {unit}/ปี"],
        line_reco=f"ตั้งเป้าปีหน้า {target}% และหาสาเหตุช่วง {TH_MONTHS[lo_m - 1]}")


def a_trend(p, value, date, by=None):
    d = p.df[[c for c in [value, date, by] if c]].dropna(subset=[date])
    d = d.assign(y=d[date].dt.year.astype(int), m=d[date].dt.month.astype(int), v=d[value] if value else 1.0)
    full = _full_years(d, date)
    Y = d.groupby("y").v.sum()
    lab = value or "จำนวนรายการ"
    best = (Y[Y.index.isin(full)] if full else Y).idxmax()
    last_full = max(full) if full else Y.index.max()
    prev = [y for y in Y.index if y < last_full and (y in full or not full)]
    yoy = (Y[last_full] / Y[prev[-1]] - 1) * 100 if prev and Y[prev[-1]] else np.nan
    M = d.groupby(["y", "m"]).v.sum().unstack(fill_value=0)
    seas = (M.loc[M.index.isin(full)] if full else M).mean()
    share = seas / seas.sum() * 100
    pk, lo = int(share.idxmax()), int(share.idxmin())
    xl = [_ylab(y, full) for y in Y.index]
    f1 = go.Figure(go.Bar(x=xl, y=Y.values, marker_color=[OK if y == best else (GREY if y not in full else BLUE) for y in Y.index],
                          text=[fmt(v) for v in Y.values], textposition="outside"))
    f2 = go.Figure()
    for y in M.index:
        f2.add_scatter(x=TH_MONTHS, y=M.loc[y].reindex(range(1, 13)).values, mode="lines", name=str(y),
                       line=dict(width=3 if y == M.index.max() else 1.4, color=AMBER if y == M.index.max() else GREY))
    f3 = go.Figure(go.Bar(x=TH_MONTHS, y=share.reindex(range(1, 13)).values, marker_color=[OK if m == pk else BAD if m == lo else GREY for m in range(1, 13)],
                          text=[f"{v:.0f}%" for v in share.reindex(range(1, 13)).values], textposition="outside"))
    if by:
        T = d.groupby(by).v.sum().sort_values(ascending=False).head(10)
        f4 = go.Figure(go.Bar(y=[str(i)[:30] for i in T.index], x=T.values, orientation="h", marker_color=BLUE, text=[fmt(v) for v in T.values], textposition="outside"))
        f4.update_yaxes(autorange="reversed")
        c4 = dict(title=f"{T.index[0]} มากที่สุด {T.iloc[0] / d.v.sum():.0%} ของทั้งหมด", sub=f"{lab} แยกตาม {by} (10 อันดับแรก)", fig=style(f4, legend=False))
    else:
        cum = d.sort_values(date).groupby(d[date].dt.to_period("M")).v.sum()
        roll = cum.rolling(3).mean()
        f4 = go.Figure(go.Scatter(x=[str(i) for i in cum.index], y=cum.values, mode="lines", line=dict(color=GREY), name="รายเดือน"))
        f4.add_scatter(x=[str(i) for i in roll.index], y=roll.values, mode="lines", line=dict(color=BLUE, width=3), name="เฉลี่ย 3 เดือน")
        c4 = dict(title=f"รายเดือนต่อเนื่อง: เดือนสูงสุด {cum.idxmax()} ({fmt(cum.max())})", sub=f"{lab} รายเดือน · เส้นหนา = เฉลี่ยเคลื่อนที่ 3 เดือน", fig=style(f4))
    for f in (f1, f3):
        f.update_xaxes(type="category")
    return result(
        answer=(f"{by} ที่มากที่สุดคือ {T.index[0]} ({T.iloc[0] / d.v.sum():.0%}) · " if by else "") + f"ปี {best} สูงสุด {fmt(Y[best])}" + (f" · ปี {last_full} เปลี่ยน {yoy:+.0f}%" if pd.notna(yoy) else "") + f" · พีคเดือน {TH_MONTHS[pk - 1]} ({share[pk]:.0f}% ของปี) — เตรียมกำลังรองรับก่อนเดือนพีค",
        kpis=[(f"{lab} ปี {last_full}", fmt(Y[last_full]), f"{yoy:+.0f}% จากปีก่อน" if pd.notna(yoy) else ""),
              ("ปีสูงสุด", str(best), fmt(Y[best])), ("เดือนพีค (เฉลี่ย)", TH_MONTHS[pk - 1], f"{share[pk]:.0f}% ของทั้งปี · ต่ำสุด {TH_MONTHS[lo - 1]} {share[lo]:.0f}%")],
        charts=[dict(title=f"ปี {best} สูงสุด {fmt(Y[best])}" + (f" · ปี {last_full} {yoy:+.0f}% จากปีก่อน" if pd.notna(yoy) else ""), sub=f"{lab} รายปี · * = ไม่ครบปี", fig=style(f1, legend=False)),
                dict(title=f"ทุกปีพีคช่วง {TH_MONTHS[pk - 1]} — ปีล่าสุด (เส้นส้ม) เทียบปีก่อน", sub=f"{lab} รายเดือนแยกปี", fig=style(f2)),
                dict(title=f"เดือน {TH_MONTHS[pk - 1]} คิดเป็น {share[pk]:.0f}% ของปี มากกว่าเดือน {TH_MONTHS[lo - 1]} {share[pk] / max(share[lo], .01):.1f} เท่า", sub="สัดส่วนเฉลี่ยของแต่ละเดือน (ปีที่ครบ)", fig=style(f3, legend=False)), c4],
        insight=f"ปริมาณไม่สม่ำเสมอตลอดปี — ช่วงพีค {TH_MONTHS[pk - 1]} สูงกว่าช่วงต่ำ {share[pk] / max(share[lo], .01):.1f} เท่า จึงต้องวางแผนกำลังคน เครื่องจักร และเงินทุนหมุนเวียนตามฤดูกาล",
        hypothesis="ความแตกต่างระหว่างปีอาจมาจากปัจจัยภายนอก (สภาพอากาศ ราคา คู่แข่ง) ที่ไม่มีในข้อมูล",
        do=f"วางแผนกำลังรองรับช่วง {TH_MONTHS[pk - 1]} ล่วงหน้า และตั้งเป้าปีหน้าอิงปี {best}",
        not_do="ไม่ใช้ค่าเฉลี่ยทั้งปีวางแผนรายเดือน", tradeoff="เตรียมกำลังรองรับพีคมีต้นทุนช่วงเดือนที่ปริมาณต่ำ",
        actions=[(f"แผนกำลังคน/เครื่องจักรช่วง {TH_MONTHS[pk - 1]}", "ผู้จัดการโรงงาน", "ก่อนพีค 2 เดือน", "รองรับพีคได้ไม่ติดคอขวด"),
                 ("ซ่อมบำรุงใหญ่ช่วงเดือนต่ำ", "ซ่อมบำรุง", f"เดือน {TH_MONTHS[lo - 1]}", "ไม่มีเครื่องหยุดช่วงพีค"),
                 ("ติดตามรายเดือนเทียบเดือนเดียวกันปีก่อน", "ฝ่ายวางแผน", "ทุกเดือน", f"ปีหน้า ≥ ปี {best}"),
                 ("ทบทวนเป้ารายไตรมาส", "ผู้บริหาร", "ทุกไตรมาส", "—")],
        limits=["ปีที่มี * ไม่ครบ 12 เดือน ไม่ใช้จัดอันดับ", "สาเหตุของการเปลี่ยนแปลงต้องใช้ข้อมูลภายนอกเพิ่ม"],
        table=M.reindex(columns=range(1, 13)).rename(columns=lambda m: TH_MONTHS[m - 1]).assign(รวม=Y).round(1),
        line_points=[f"ปี {best} สูงสุด {fmt(Y[best])}" + (f" ปี {last_full} {yoy:+.0f}%" if pd.notna(yoy) else ""), f"พีคเดือน {TH_MONTHS[pk - 1]} ({share[pk]:.0f}% ของปี)"],
        line_reco=f"เตรียมกำลังรองรับก่อนเดือน {TH_MONTHS[pk - 1]}")


def a_gap(p, group, amount, qty, date, site=None):
    keys = [date] + ([site] if site else [])
    d = p.df[[c for c in [group, amount, qty, date, site] if c]].dropna()
    d = d[d[qty] > 0]
    vol = d.groupby(group)[qty].sum().sort_values(ascending=False)
    if len(vol) < 2:
        raise ValueError(f"'{group}' มีข้อมูลแค่กลุ่มเดียวหลังตัดค่าว่าง")
    ref = vol.index[0]
    refp = d[d[group] == ref].groupby(keys).apply(lambda x: x[amount].sum() / x[qty].sum(), include_groups=False).rename("rp")
    o = d[d[group] != ref].join(refp, on=keys).dropna(subset=["rp"])
    if o.empty:
        raise ValueError(f"ไม่มีวันที่กลุ่มอื่นซื้อขายพร้อมกลุ่มหลัก '{ref}' ให้เทียบ")
    o["prem"] = o[amount] - o[qty] * o.rp
    o["y"], o["q"] = o[date].dt.year, o[date].dt.to_period("Q")
    full = _full_years(d, date)
    Yp = o.groupby("y").apply(lambda x: pd.Series({"paid": x.prem.sum(), "perunit": x.prem.sum() / x[qty].sum(), "qty": x[qty].sum()}), include_groups=False)
    share = d.assign(y=d[date].dt.year).groupby(["y", group])[qty].sum().unstack(fill_value=0)
    share = share.div(share.sum(axis=1), axis=0)
    Q = o.groupby(["q", group]).apply(lambda x: x.prem.sum() / x[qty].sum(), include_groups=False).unstack()
    last = Yp.index.max()
    L = Yp.loc[last]
    main_other = vol.index[1]
    xl = [_ylab(y, full) for y in share.index]
    f1 = go.Figure()
    for g in share.columns:
        f1.add_bar(x=xl, y=share[g] * 100, name=str(g)[:25])
    f1.update_layout(barmode="stack")
    f1.update_yaxes(title="%")
    f2 = go.Figure()
    for g in Q.columns:
        f2.add_scatter(x=[str(q) for q in Q.index], y=Q[g], mode="lines+markers", name=str(g)[:25])
    f2.add_hline(y=0, line=dict(color="#56657E", width=1))
    f2.update_yaxes(title="ต่อหน่วย")
    yl = [_ylab(y, full) for y in Yp.index]
    f3 = go.Figure(go.Bar(x=yl, y=Yp.paid, marker_color=[BAD if v > 0 else OK for v in Yp.paid], text=[fmt(v) for v in Yp.paid], textposition="outside"))
    f4 = go.Figure()
    for g in o[group].unique()[:6]:                       # กล่องจากค่าสรุป (ควอไทล์) ไม่ส่งค่ารายรายการขึ้นหน้าเว็บ
        v = o[o[group] == g].pipe(lambda x: x[amount] / x[qty] - x.rp).dropna()
        q1, md, q3 = v.quantile([.25, .5, .75])
        iqr = q3 - q1
        f4.add_box(name=str(g)[:25], q1=[q1], median=[md], q3=[q3], mean=[v.mean()],
                   lowerfence=[max(v.min(), q1 - 1.5 * iqr)], upperfence=[min(v.max(), q3 + 1.5 * iqr)], boxpoints=False)
    for f in (f1, f3):
        f.update_xaxes(type="category")
    return result(
        answer=f"ปี {last} กลุ่มอื่นต่างจากกลุ่มหลัก ({ref}) เฉลี่ย {L.perunit:+.3f} ต่อหน่วย รวม {fmt(L.paid)} — ต้องเทียบกับสิ่งที่ได้กลับมา (เช่น ราคาขาย) ก่อนตัดสินใจ",
        kpis=[(f"สัดส่วน '{main_other}' ปี {last}", f"{share.loc[last, main_other]:.0%}" if last in share.index else "—", f"ปีแรก {share[main_other].iloc[0]:.0%}"),
              (f"ส่วนต่างต่อหน่วย ปี {last}", f"{L.perunit:+.3f}", f"ปีแรก {Yp.perunit.iloc[0]:+.3f}"),
              (f"ส่วนต่างรวม ปี {last}", fmt(L.paid), f"รวมทุกปี {fmt(Yp.paid.sum())}")],
        charts=[dict(title=f"'{main_other}' เพิ่มจาก {share[main_other].iloc[0]:.0%} เป็น {share[main_other].iloc[-1]:.0%}", sub=f"สัดส่วนปริมาณ ({qty}) แยกตาม {group}", fig=style(f1)),
                dict(title=f"ส่วนต่างต่อหน่วยเปลี่ยนจาก {Yp.perunit.iloc[0]:+.3f} เป็น {L.perunit:+.3f}", sub=f"รายไตรมาส เทียบราคากลุ่ม '{ref}' วันเดียวกัน" + (" จุดเดียวกัน" if site else ""), fig=style(f2)),
                dict(title=f"ส่วนต่างรวม {fmt(Yp.paid.sum())} · ปี {last} {fmt(L.paid)}", sub="ผลรวมต่อปี (ราคา × ปริมาณ เทียบกลุ่มหลัก) · * = ไม่ครบปี", fig=style(f3, legend=False)),
                dict(title="การกระจายของส่วนต่างต่อหน่วยรายรายการ", sub="ราคาต่อหน่วยของรายการ − ราคากลุ่มหลักวันเดียวกัน · เส้นประ = ค่าเฉลี่ย", fig=style(f4, legend=False))],
        insight=f"ส่วนต่างต่อหน่วย{'เพิ่มขึ้น' if L.perunit > Yp.perunit.iloc[0] else 'ลดลง'}และปริมาณกลุ่ม '{main_other}' {'เพิ่ม' if share[main_other].iloc[-1] > share[main_other].iloc[0] else 'ลด'} — ภาระรวมจึง{'โตเร็ว' if L.paid > Yp.paid.iloc[0] else 'ลดลง'}",
        hypothesis="ส่วนต่างที่เปลี่ยนไปอาจมาจากการแข่งขันในตลาด ไม่ใช่จากมูลค่าที่ได้กลับมาเพิ่ม",
        do="ผูกส่วนต่างที่จ่ายกับมูลค่าที่ได้กลับมาจริง (เช่น ราคาขายส่วนเพิ่ม) และทบทวนทุกเดือน",
        not_do="ไม่ขึ้นส่วนต่างตามตลาดโดยไม่มีรายได้รองรับ", tradeoff="ลดส่วนต่างอาจเสียคู่ค้าให้คู่แข่ง",
        actions=[("ดึงข้อมูลรายได้ส่วนเพิ่มที่ได้จริงมาเทียบ", "ฝ่ายขาย + บัญชี", "14 วัน", "รู้จุดคุ้มทุน"),
                 ("ตั้งสูตรส่วนต่างให้ผูกกับรายได้ส่วนเพิ่ม", "CFO + ฝ่ายจัดซื้อ", "ไตรมาสถัดไป", "ผลสุทธิ ≥ 0"),
                 ("ติดตามสัดส่วนและจำนวนคู่ค้าหลังปรับ", "ฝ่ายจัดซื้อ", "รายเดือน", "สัดส่วนไม่ลดเกิน 5 จุด"),
                 ("ทบทวนรายไตรมาส", "ผู้บริหาร", "ทุกไตรมาส", "—")],
        limits=[f"เทียบกับกลุ่มหลัก '{ref}' ในวันเดียวกัน" + (" และจุดเดียวกัน" if site else ""), "ยังไม่รวมรายได้ส่วนเพิ่มที่ได้กลับมา — ต้องใช้ข้อมูลขายประกอบ"],
        table=Yp.assign(**{"ปี": [_ylab(y, full) for y in Yp.index]}).set_index("ปี").round(3),
        line_points=[f"ปี {last} ส่วนต่าง {L.perunit:+.3f}/หน่วย รวม {fmt(L.paid)}", f"สัดส่วน '{main_other}' {share[main_other].iloc[0]:.0%} → {share[main_other].iloc[-1]:.0%}"],
        line_reco="ผูกส่วนต่างที่จ่ายกับรายได้ที่ได้กลับมาจริง")


def a_conc(p, entity, value, date=None):
    d = p.df[[c for c in [entity, value, date] if c]].dropna(subset=[entity, value])
    s = d.groupby(entity)[value].sum().sort_values(ascending=False)
    s = s[s > 0]
    n = len(s)
    cum = s.cumsum() / s.sum()
    top20 = s.head(max(1, int(n * .2))).sum() / s.sum()
    top10 = s.head(10).sum() / s.sum()
    lost_t, lost_n, new_n, lost_sh, yrs = np.nan, 0, 0, np.nan, []
    if date:
        y = d[date].dt.year
        yrs = sorted(y.dropna().unique())
        if len(yrs) >= 2:
            a, b = yrs[-2], yrs[-1]
            sa, sb = set(d[y == a][entity]), set(d[y == b][entity])
            va = d[y == a].groupby(entity)[value].sum()
            lost = sa - sb
            lost_n, new_n = len(lost), len(sb - sa)
            lost_t, lost_sh = va[list(lost)].sum(), va[list(lost)].sum() / va.sum()
    f1 = go.Figure(go.Scatter(x=np.arange(1, n + 1) / n * 100, y=cum.values * 100, mode="lines", line=dict(color=BLUE, width=3)))
    f1.add_vline(x=20, line=dict(color=AMBER, dash="dot"))
    f1.update_xaxes(title="% ของจำนวนราย (เรียงจากมากไปน้อย)")
    f1.update_yaxes(title=f"% ของ {value} สะสม")
    t10 = s.head(10)
    f2 = go.Figure(go.Bar(y=[f"ราย {i}" for i in range(1, len(t10) + 1)], x=t10.values / s.sum() * 100, orientation="h", marker_color=BLUE,
                          text=[f"{v / s.sum():.1%}" for v in t10.values], textposition="outside"))
    f2.update_yaxes(autorange="reversed")
    charts = [dict(title=f"ราย 20% แรกคิดเป็น {top20:.0%} ของ {value}", sub="Pareto: สัดส่วนสะสม", fig=style(f1, legend=False)),
              dict(title=f"10 รายแรกรวม {top10:.0%} · รายใหญ่สุด {s.iloc[0] / s.sum():.1%}", sub="10 อันดับแรก (ไม่แสดงชื่อ)", fig=style(f2, legend=False))]
    if date and len(yrs) >= 2:
        y = d[date].dt.year
        act = pd.Series({yy: d[y == yy][entity].nunique() for yy in yrs})
        newc = pd.Series({yy: len(set(d[y == yy][entity]) - set(d[y < yy][entity])) if i else np.nan for i, yy in enumerate(yrs)})
        f3 = go.Figure()
        f3.add_bar(x=[str(v) for v in yrs], y=act.values, name="รายที่มีความเคลื่อนไหว", marker_color=GREY)
        f3.add_bar(x=[str(v) for v in yrs], y=newc.values, name="รายใหม่", marker_color=OK)
        f3.update_layout(barmode="group")
        lostv = []
        for i in range(1, len(yrs)):
            a, b = yrs[i - 1], yrs[i]
            va = d[y == a].groupby(entity)[value].sum()
            lostv.append(va[list(set(va.index) - set(d[y == b][entity]))].sum())
        f4 = go.Figure(go.Bar(x=[f"{yrs[i - 1]}→{yrs[i]}" for i in range(1, len(yrs))], y=lostv, marker_color=BAD, text=[fmt(v) for v in lostv], textposition="outside"))
        for f in (f3, f4):
            f.update_xaxes(type="category")
        charts += [dict(title=f"ปี {yrs[-1]} มีรายใหม่ {new_n} ราย · หายไป {lost_n} ราย", sub="จำนวนรายต่อปี", fig=style(f3)),
                   dict(title=f"รายที่หายไปปีล่าสุดเคยมี {fmt(lost_t)} ({lost_sh:.1%})", sub=f"{value} ของรายที่ไม่กลับมาในปีถัดไป", fig=style(f4, legend=False))]
    return result(
        answer=f"ราย 20% แรกคิดเป็น {top20:.0%} ของ {value}" + (f" · ปีล่าสุดเสีย {lost_n} ราย ({lost_sh:.1%})" if lost_n else "") + " — ควรมีโครงการรักษารายใหญ่",
        kpis=[("ราย 20% แรก", f"{top20:.0%}", f"จาก {n:,} ราย"), ("รายใหญ่สุด", f"{s.iloc[0] / s.sum():.1%}", f"10 รายแรก {top10:.0%}"),
              ("รายที่หายไปปีล่าสุด", f"{lost_n:,} ราย" if date else "—", f"{fmt(lost_t)} ({lost_sh:.1%})" if lost_n else "")],
        charts=charts,
        insight="ปริมาณกระจุกที่รายใหญ่จำนวนน้อย — การเสียรายใหญ่ 1 รายกระทบมากกว่ารายเล็กหลายสิบราย",
        hypothesis="รายที่หายไปอาจย้ายไปคู่แข่งเพราะราคา/บริการ — ต้องสอบถามโดยตรง",
        do="ทำรายชื่อรายใหญ่กลุ่มเสี่ยง (ปริมาณลดลง) และติดต่อก่อนจะหายไป",
        not_do="ไม่ให้สิทธิพิเศษทุกรายเท่ากัน", tradeoff="สิทธิพิเศษให้รายใหญ่มีต้นทุน และอาจทำให้รายเล็กไม่พอใจ",
        actions=[("รายชื่อรายใหญ่ที่ปริมาณลดลงเกิน 30%", "ฝ่ายขาย/จัดซื้อ", "14 วัน", "ครบทุกรายใน 20% แรก"),
                 ("โทรติดตามรายที่หายไปปีล่าสุด", "ฝ่ายขาย/จัดซื้อ", "30 วัน", f"ได้กลับมา ≥ 20% ของ {lost_n} ราย"),
                 ("ติดตามรายเดือน: รายใหม่ / รายที่หาย", "ฝ่ายวิเคราะห์", "รายเดือน", "รายที่หาย < ปีก่อน"),
                 ("ทบทวนโครงการรักษาราย", "ผู้บริหาร", "ทุกไตรมาส", "—")],
        limits=["ไม่แสดงชื่อหรือรหัส — ใช้ลำดับ 'ราย 1, 2, …'", "รายที่หาย = มีในปีก่อนแต่ไม่มีในปีล่าสุด (ปีล่าสุดอาจยังไม่ครบ)"],
        table=pd.DataFrame({"ลำดับ": [f"ราย {i}" for i in range(1, min(20, n) + 1)], value: s.head(20).values.round(1), "สัดส่วน": (s.head(20).values / s.sum()).round(4)}).set_index("ลำดับ"),
        line_points=[f"ราย 20% แรก = {top20:.0%} ของทั้งหมด", f"ปีล่าสุดเสีย {lost_n} ราย ({lost_sh:.1%})" if lost_n else f"รายใหญ่สุด {s.iloc[0] / s.sum():.1%}"],
        line_reco="ทำรายชื่อรายใหญ่กลุ่มเสี่ยงและติดต่อก่อนหายไป")


def a_threshold(p, value, date, threshold=None):
    d = p.df[[value, date]].dropna().sort_values(date)
    s = d.groupby(d[date].dt.normalize())[value].last()
    th = threshold or float(np.round(s.quantile(.25), -int(max(0, np.floor(np.log10(max(abs(s.quantile(.25)), 1))) - 1))))
    y = s.index.year
    last_y = y.max()
    S = s[y == last_y]
    below = (s < th).groupby(y).sum()
    ch10 = s.diff(10).dropna()
    p95 = -ch10.quantile(.05)
    f1 = go.Figure(go.Scatter(x=S.index, y=S.values, mode="lines", line=dict(color=BLUE, width=2), fill="tozeroy", fillcolor="rgba(47,111,222,.08)"))
    f1.add_hline(y=th, line=dict(color=AMBER, dash="dot"), annotation_text=f"เกณฑ์ {fmt(th)}")
    f2 = go.Figure(go.Bar(x=[str(v) for v in below.index], y=below.values, marker_color=[BAD if v else OK for v in below.values], text=below.values, textposition="outside"))
    cnt, edges = np.histogram(-ch10.clip(upper=0), bins=40)
    f3 = go.Figure(go.Bar(x=(edges[:-1] + edges[1:]) / 2, y=cnt, marker_color=GREY))
    f3.add_vline(x=p95, line=dict(color=AMBER, dash="dot"), annotation_text="95%")
    mo = s.groupby(s.index.month).mean()
    f4 = go.Figure(go.Bar(x=[TH_MONTHS[m - 1] for m in mo.index], y=mo.values, marker_color=[BAD if v == mo.min() else GREY for v in mo.values]))
    for f in (f2, f4):
        f.update_xaxes(type="category")
    return result(
        answer=f"ปี {last_y} ต่ำกว่าเกณฑ์ {fmt(th)} รวม {int(below.get(last_y, 0))} วัน · ช่วง 10 วันที่แย่ 5% ลดลงถึง {fmt(p95)} → ควรตั้งขั้นต่ำ ≥ {fmt(p95)} และมีวงเงินสำรอง",
        kpis=[(f"{value} ล่าสุด ({S.index.max():%d %b %Y})", fmt(S.iloc[-1]), f"{S.iloc[-1] - th:+,.0f} เทียบเกณฑ์"),
              (f"วันที่ต่ำกว่าเกณฑ์ ปี {last_y}", f"{int(below.get(last_y, 0))} วัน", f"ต่ำสุด {fmt(S.min())} ({S.idxmin():%d %b})"),
              ("ลดลงใน 10 วัน (แย่ 5%)", fmt(p95), "ใช้ตั้งขั้นต่ำ")],
        charts=[dict(title=f"ปี {last_y} ต่ำสุด {fmt(S.min())} วันที่ {S.idxmin():%d %b}", sub=f"{value} รายวัน · เส้นประ = เกณฑ์", fig=style(f1, legend=False)),
                dict(title=f"ปี {last_y} ต่ำกว่าเกณฑ์ {int(below.get(last_y, 0))} วัน", sub="จำนวนวันต่ำกว่าเกณฑ์ต่อปี", fig=style(f2, legend=False)),
                dict(title=f"95% ของช่วง 10 วัน ลดลงไม่เกิน {fmt(p95)}", sub="การลดลงสะสม 10 วัน", fig=style(f3, legend=False)),
                dict(title=f"เดือน {TH_MONTHS[int(mo.idxmin()) - 1]} ระดับต่ำสุดเฉลี่ย — วางแผนล่วงหน้าได้", sub="ค่าเฉลี่ยรายเดือน", fig=style(f4, legend=False))],
        insight="ปัญหาอยู่ที่จังหวะเข้า-ออกไม่ตรงกันและไม่มีเกณฑ์ขั้นต่ำ ไม่ใช่ยอดรวมไม่พอ",
        hypothesis="เดือนที่ต่ำซ้ำทุกปีมาจากรอบการจ่ายตามฤดูกาล",
        do=f"ตั้งขั้นต่ำ {fmt(max(th, p95))} และวงเงินสำรองเท่าส่วนที่ขาด · ทำประมาณการ 4 สัปดาห์",
        not_do="ไม่แก้ปัญหาแบบเร่งด่วนทีละครั้ง", tradeoff="วงเงินสำรองมีค่าธรรมเนียมผูกพัน",
        actions=[("ประมาณการ 4 สัปดาห์ ทบทวนทุกวันจันทร์", "ผู้จัดการการเงิน", "เริ่มทันที", f"วันต่ำกว่าเกณฑ์ {int(below.get(last_y, 0))} → 0"),
                 ("เจรจาวงเงินสำรอง", "CFO", "30 วัน", "มีวงเงินพร้อมใช้"),
                 (f"เตรียมล่วงหน้าก่อนเดือน {TH_MONTHS[int(mo.idxmin()) - 1]}", "การเงิน", "ทุกปี", "ไม่ต่ำกว่าเกณฑ์"),
                 ("ทบทวนเกณฑ์รายไตรมาส", "ผู้บริหาร", "ทุกไตรมาส", "—")],
        limits=[f"เกณฑ์ตั้งต้น = ค่าควอไทล์ล่าง ({fmt(th)}) ปรับได้", "ข้อมูลหลังวันนี้อาจเป็นรายการล่วงหน้า"],
        table=pd.DataFrame({"วันที่ต่ำกว่าเกณฑ์": below, "ต่ำสุด": s.groupby(y).min().round(0)}),
        line_points=[f"ปี {last_y} ต่ำกว่าเกณฑ์ {int(below.get(last_y, 0))} วัน", f"ต่ำสุด {fmt(S.min())} ({S.idxmin():%d %b})"],
        line_reco=f"ตั้งขั้นต่ำ {fmt(max(th, p95))} + วงเงินสำรอง")


def a_relation(p, x, y, date):
    d = p.df[[x, y, date]].dropna()
    d = d.assign(ym=d[date].dt.to_period("M"))
    M = d.groupby("ym")[[x, y]].mean()
    r_m = M[x].corr(M[y])
    if d.groupby("ym").size().max() <= 1:
        # ข้อมูลรายเดือน (เดือนละแถว) หาค่าภายในเดือนไม่ได้ → ตัดผลฤดูกาลด้วยการเทียบเดือนเดียวกันของปีอื่นแทน
        dev = M - M.groupby(M.index.month).transform("mean")
        in_lab, in_sub = "หลังตัดผลฤดูกาล", "เทียบค่าเฉลี่ยของเดือนเดียวกันทุกปี"
    else:
        dev = d[[x, y]] - d.groupby("ym")[[x, y]].transform("mean")
        in_lab, in_sub = "ภายในเดือนเดียวกัน", "เทียบค่าเฉลี่ยของเดือน"
    r_in = dev[x].corr(dev[y])
    if pd.isna(r_in):
        raise ValueError("ข้อมูลไม่พอดูความสัมพันธ์หลังตัดผลฤดูกาล (ต้องมีหลายรายการต่อเดือน หรือข้อมูลหลายปี)")
    try:
        dev["b"] = pd.qcut(dev[x], 5, labels=False, duplicates="drop")
        Qb = dev.groupby("b")[y].mean()
    except ValueError:
        Qb = pd.Series(dtype=float)
    f1 = go.Figure(go.Scatter(x=M[x], y=M[y], mode="markers+text", text=[str(i) for i in M.index], textposition="top center",
                              textfont=dict(size=9, color="#8A97AB"), marker=dict(size=9, color=BLUE)))
    f1.update_xaxes(title=x)
    f1.update_yaxes(title=y)
    f2 = go.Figure(go.Bar(x=[f"กลุ่ม {int(i) + 1}" for i in Qb.index], y=Qb.values, marker_color=[OK if v >= 0 else BAD for v in Qb.values],
                          text=[f"{v:+.3g}" for v in Qb.values], textposition="outside"))
    f3 = go.Figure()
    f3.add_scatter(x=[str(i) for i in M.index], y=(M[x] - M[x].mean()) / M[x].std(), name=x, mode="lines", line=dict(color=BLUE))
    f3.add_scatter(x=[str(i) for i in M.index], y=(M[y] - M[y].mean()) / M[y].std(), name=y, mode="lines", line=dict(color=AMBER))
    cnt, edges = np.histogram(d[x].dropna(), bins=30)     # นับเป็นช่วงในเครื่อง ไม่ส่งค่ารายรายการขึ้นหน้าเว็บ
    f4 = go.Figure(go.Bar(x=(edges[:-1] + edges[1:]) / 2, y=cnt, marker_color=GREY))
    strong = abs(r_in) >= .3
    return result(
        answer=f"รายเดือน r = {r_m:+.2f} · {in_lab} r = {r_in:+.2f} — {'สัมพันธ์จริงในระดับที่ใช้ทดลองต่อได้' if strong else 'ความสัมพันธ์อ่อน ยังไม่ควรใช้ตัดสินใจ'} (ยังไม่ใช่เหตุและผล)",
        kpis=[("r รายเดือน", f"{r_m:+.2f}", f"{len(M)} เดือน"), (f"r {in_lab}", f"{r_in:+.2f}", "ตัดผลฤดูกาล/ปี"),
              (f"{y} กลุ่ม {x} สูงสุด − ต่ำสุด", f"{(Qb.iloc[-1] - Qb.iloc[0]):+.3g}" if len(Qb) > 1 else "—", "แบ่ง 5 กลุ่มเท่ากัน")],
        charts=[dict(title=f"รายเดือน {x} กับ {y}: r = {r_m:+.2f}", sub="แต่ละจุด = 1 เดือน", fig=style(f1, legend=False)),
                dict(title=f"{in_lab}: เมื่อ {x} สูงขึ้น {y} {'ลดลง' if r_in < 0 else 'สูงขึ้น'} (r = {r_in:+.2f})", sub=f"{y} {in_sub} แบ่งตาม {x} 5 กลุ่ม (ต่ำ → สูง)", fig=style(f2, legend=False)),
                dict(title="แนวโน้มรายเดือนของทั้งสองตัว (ปรับสเกล)", sub="ค่ามาตรฐาน (z-score)", fig=style(f3)),
                dict(title=f"การกระจายของ {x}", sub="จำนวนรายการ", fig=style(f4, legend=False))],
        insight=f"ความสัมพันธ์{'ชัด' if strong else 'อ่อน'} — ระวัง: รายเดือนอาจหลอกตาเพราะฤดูกาล ดูค่า{in_lab}เป็นหลัก",
        hypothesis=f"{x} อาจส่งผลต่อ {y} หรือทั้งคู่ถูกขับด้วยปัจจัยร่วม", do="ทดลองแบบควบคุม (สลับวัน/กลุ่ม) ก่อนสรุปเป็นเหตุและผล",
        not_do="ไม่เปลี่ยนนโยบายจากความสัมพันธ์อย่างเดียว", tradeoff="การทดลองใช้เวลา 4–6 สัปดาห์",
        actions=[("ออกแบบการทดลองสลับรายวัน", "ฝ่ายผลิต/QC", "14 วัน", "แผนทดลองอนุมัติ"),
                 ("เก็บข้อมูลทดลอง", "ฝ่ายผลิต", "4–6 สัปดาห์", "ครบทุกวัน"), ("สรุปผล + ตัดสินใจ", "ผู้จัดการโรงงาน", "หลังทดลอง", "ผลต่างมีนัย"),
                 ("ทบทวน", "ผู้บริหาร", "ทุกไตรมาส", "—")],
        limits=["ความสัมพันธ์ไม่ใช่เหตุและผล", "ค่าเฉลี่ยรายเดือนมีผลฤดูกาลปน"],
        table=M.round(3), line_points=[f"รายเดือน r = {r_m:+.2f}", f"{in_lab} r = {r_in:+.2f}"],
        line_reco="ทดลองแบบควบคุมก่อนเปลี่ยนนโยบาย")


def a_quality(p, date=None):
    df = p.df
    miss = pd.Series({c.name: 1 - c.nonnull for c in p.cols.values() if c.name != "_sheet"}).sort_values(ascending=False)
    dup = int(df.duplicated().sum())
    const = {}
    for c in p.cols.values():
        if c.kind in ("number", "category") and c.name != "_sheet":
            vc = df[c.name].value_counts(normalize=True, dropna=True)
            if len(vc) and vc.iloc[0] > .9:
                const[c.name] = vc.iloc[0]
    const = pd.Series(const).sort_values(ascending=False)
    complete = 1 - miss.mean()
    gaps, per_m = [], None
    if date:
        per_m = df.groupby(df[date].dt.to_period("M")).size()
        allm = pd.period_range(per_m.index.min(), per_m.index.max(), freq="M")
        gaps = [str(m) for m in allm if m not in per_m.index]
        per_m = per_m.reindex(allm, fill_value=0)
    f1 = go.Figure(go.Bar(y=miss.head(12).index.astype(str), x=miss.head(12).values * 100, orientation="h",
                          marker_color=[BAD if v > .3 else AMBER if v > .05 else OK for v in miss.head(12).values], text=[f"{v:.0%}" for v in miss.head(12).values], textposition="outside"))
    f1.update_yaxes(autorange="reversed")
    f1.update_xaxes(title="% ค่าว่าง", range=[0, 110])
    charts = [dict(title=f"ข้อมูลครบเฉลี่ย {complete:.0%} · คอลัมน์ที่ว่างมากสุด '{miss.index[0]}' {miss.iloc[0]:.0%}", sub="% ค่าว่างต่อคอลัมน์ (12 อันดับแรก)", fig=style(f1, legend=False))]
    if per_m is not None:
        f2 = go.Figure(go.Bar(x=[str(i) for i in per_m.index], y=per_m.values, marker_color=[BAD if v == 0 else GREY for v in per_m.values]))
        charts.append(dict(title=f"ขาดข้อมูล {len(gaps)} เดือน" if gaps else "มีข้อมูลทุกเดือน", sub="จำนวนรายการต่อเดือน · แดง = ไม่มีข้อมูล", fig=style(f2, legend=False)))
    if len(const):
        f3 = go.Figure(go.Bar(y=const.head(10).index.astype(str), x=const.head(10).values * 100, orientation="h", marker_color=AMBER, text=[f"{v:.0%}" for v in const.head(10).values], textposition="outside"))
        f3.update_yaxes(autorange="reversed")
        f3.update_xaxes(range=[0, 110])
        charts.append(dict(title=f"{len(const)} คอลัมน์มีค่าเดียวเกิน 90% — อาจเป็นค่าตั้ง ไม่ใช่ค่าที่วัด", sub="% ของค่าที่พบบ่อยที่สุด", fig=style(f3, legend=False)))
    issues = int((miss > .3).sum()) + len(gaps) + (1 if dup else 0) + len(const)
    return result(
        answer=f"ข้อมูลครบเฉลี่ย {complete:.0%} · ซ้ำ {dup:,} แถว · ขาด {len(gaps)} เดือน · {len(const)} คอลัมน์ค่าเดียว — {'แก้ก่อนใช้ตั้ง KPI' if issues else 'พร้อมใช้'}",
        kpis=[("ข้อมูลครบเฉลี่ย", f"{complete:.0%}", f"{int((miss > .3).sum())} คอลัมน์ว่างเกิน 30%"), ("แถวซ้ำ", f"{dup:,}", f"จาก {p.n:,} แถว"),
              ("เดือนที่ขาดข้อมูล", f"{len(gaps)}", ", ".join(gaps[:4]) + (" …" if len(gaps) > 4 else ""))],
        charts=charts, insight="ช่องว่างและค่าที่ซ้ำเดิมทุกวันทำให้ตัวเลข KPI คลาดเคลื่อน — ต้องแก้ที่การบันทึก ไม่ใช่ที่การวิเคราะห์",
        hypothesis="ค่าที่เหมือนกันทุกวันน่าจะเป็นค่าตั้งที่คัดลอก ไม่ใช่ค่าที่วัดจริง",
        do="กำหนดแบบฟอร์มบันทึกเดียว หน่วยเดียว บังคับกรอกช่องสำคัญ และตรวจทุกสิ้นเดือน", not_do="ไม่ใช้ข้อมูลชุดนี้ตั้ง KPI ก่อนแก้",
        tradeoff="ผู้บันทึกต้องใช้เวลาเพิ่ม",
        actions=[("กำหนดช่องบังคับกรอกและหน่วยมาตรฐาน", "หัวหน้าแผนก", "14 วัน", "ค่าว่างช่องสำคัญ < 2%"),
                 ("ตรวจข้อมูลทุกสิ้นเดือน", "ฝ่ายวิเคราะห์", "รายเดือน", "ไม่มีเดือนขาด"),
                 ("บันทึกค่าที่วัดจริง ไม่ใช่ค่าตั้ง", "ผู้ปฏิบัติงาน", "เริ่มทันที", "คอลัมน์ค่าเดียว → 0"),
                 ("ทบทวนความพร้อมข้อมูล", "ผู้บริหาร", "ทุกไตรมาส", "—")],
        limits=["ตรวจเฉพาะรูปแบบข้อมูล ไม่ได้ตรวจกับเอกสารต้นทาง"],
        table=pd.DataFrame({"% ค่าว่าง": (miss * 100).round(1)}),
        line_points=[f"ข้อมูลครบเฉลี่ย {complete:.0%} ขาด {len(gaps)} เดือน", f"{len(const)} คอลัมน์เป็นค่าเดียวเกือบทั้งหมด"],
        line_reco="ตั้งแบบฟอร์มบันทึกเดียวและตรวจทุกสิ้นเดือน")
