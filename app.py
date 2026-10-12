"""Decision Brief AI — แนบไฟล์ → เสนอ 5 โจทย์ → วิเคราะห์ → dashboard สรุปผู้บริหาร → ข้อความ LINE

รัน:  python -m streamlit run app.py --server.port 8600
วิเคราะห์ในเครื่องทั้งหมด · ไม่ขึ้นเว็บให้อัตโนมัติ — ผู้ใช้ดาวน์โหลดหน้า dashboard (HTML) ไปแนบเอง
"""
import hashlib
import html
import json
import re
import sys

import streamlit as st

from engine.analysis import TYPES, guess_type
from engine.classify import classify
from engine.crossfile import TYPE as CROSS, load_steps, propose_all, run_any
from engine.data_io import sensitive_cols
from engine.publish import LINE_MAX_CHARS, build_html, download_html, line_copy_html, line_message

APP = "Decision Brief AI"
# บนเว็บ (Cloudflare Pages) แอปรันในเบราว์เซอร์ผ่าน stlite — ไฟล์ข้อมูลไม่ออกจากเครื่องผู้ใช้
IN_BROWSER = sys.platform == "emscripten"
SITE = json.load(open("site.json", encoding="utf-8")) if IN_BROWSER else {}
st.set_page_config(page_title=APP, page_icon="📊", layout="wide")
st.markdown("""
<style>
.block-container{max-width:1200px;padding-top:1.4rem}
.step{display:flex;gap:8px;margin:0 0 14px 0;flex-wrap:wrap}
.step span{padding:4px 12px;border-radius:999px;background:#EEF1F6;color:#56657E;font-size:.88rem}
.step span.on{background:#2F6FDE;color:#fff;font-weight:600}
.step span.done{background:#DCEBFF;color:#2F6FDE}
.prob{border:1px solid #E3E8F0;border-radius:12px;padding:10px 14px;margin-bottom:8px;background:#fff}
.wbar{height:8px;border-radius:4px;background:#2F6FDE}
</style>""", unsafe_allow_html=True)

S = st.session_state
S.setdefault("step", 1)
STEPS = ["1 แนบไฟล์", "2 เลือกโจทย์", "3 วิเคราะห์ + dashboard", "4 ข้อความ LINE"]


# คำในโจทย์ภาษาพูด → คำที่มักอยู่ในชื่อคอลัมน์
SYN = {"ผลผลิต": "output yield ผลิต", "วัตถุดิบ": "raw material input", "ของเสีย": "waste scrap reject", "ไฟฟ้า": "electric power kwh",
       "ต้นทุน": "cost", "กำไร": "profit margin", "ราคา": "price", "ยอดขาย": "amount ยอด", "น้ำหนัก": "weight", "ลูกค้า": "customer", "เงิน": "amount ยอด"}
UNIT_WORDS = {"ตัน", "kg", "กก", "บาท", "ton", "tons"}


def rank(cols: list, q: str) -> list:
    """เรียงคอลัมน์ตามจำนวนคำในชื่อที่ตรงกับโจทย์ (คงลำดับเดิมเมื่อคะแนนเท่ากัน)"""
    text = (q or "").lower()
    text += " " + " ".join(v for k, v in SYN.items() if k in text).lower()
    def score(c):
        toks = {t.lower() for t in re.findall(r"[A-Za-z]{2,}|[ก-๙]{2,}", c)} - UNIT_WORDS
        return sum(t in text for t in toks)
    return sorted(cols, key=score, reverse=True)


def premium_settings(prob: dict) -> dict:
    """ค่าที่ผู้ใช้ปรับได้ของโจทย์ข้ามไฟล์: อัตราแปลงวัตถุดิบ → สินค้า และเป้าสัดส่วนที่ขายเป็นพรีเมียม"""
    pr = dict(prob["params"])
    c1, c2 = st.columns(2)
    conv = pr["conv"] if pr["conv"] == pr["conv"] else 0.0                  # NaN = ให้ระบบประมาณ
    pr["conv"] = c1.number_input("อัตราแปลงวัตถุดิบ → สินค้า (%) · 0 = ให้ระบบประมาณจากข้อมูล", 0.0, 100.0, float(conv), 0.1,
                                 help=pr.get("conv_src") or "แนบไฟล์การผลิต (เช่น ผลผลิต / วัตถุดิบที่ใช้) เพื่อใช้ % จริง") or float("nan")
    pr["target"] = int(c2.number_input("เป้าสัดส่วนที่ขายเป็นพรีเมียม (%)", 10, 100, int(pr.get("target", 90)), 5))
    if pr["conv"] == pr["conv"] and pr["conv"] != conv:
        pr["conv_src"] = "ผู้ใช้กำหนด"
    return dict(prob, params=pr)


def stepbar():
    h = "".join(f'<span class="{"on" if i + 1 == S.step else "done" if i + 1 < S.step else ""}">{t}</span>' for i, t in enumerate(STEPS))
    st.markdown(f'<div class="step">{h}</div>', unsafe_allow_html=True)


st.title(APP)
st.caption("แนบไฟล์ข้อมูล → ระบบเสนอโจทย์ 5 ข้อพร้อมน้ำหนัก → วิเคราะห์ตามหลักการวิเคราะห์ข้อมูลเพื่อการตัดสินใจ → dashboard สรุปผู้บริหาร → ข้อความ LINE")
stepbar()

# ============================================================ 1. แนบไฟล์
ups = st.file_uploader("แนบไฟล์ข้อมูล (.xlsx .csv .txt .tsv — 1 แถว = 1 รายการ · Excel หลายชีตได้ · แนบหลายไฟล์ได้ เช่น ไฟล์ขาย + ไฟล์รับซื้อ)",
                       type=["xlsx", "xlsm", "csv", "txt", "tsv"], accept_multiple_files=True)
if not ups:
    S.step = 1
    st.info(("ไฟล์จะถูกวิเคราะห์ในเบราว์เซอร์ของคุณเท่านั้น ไม่ถูกส่งขึ้นเซิร์ฟเวอร์" if IN_BROWSER else "ไฟล์จะถูกวิเคราะห์ในเครื่องนี้เท่านั้น")
            + " · ไฟล์ export หลายปีที่หัวตารางเหมือนกันจะรวมเป็นชุดเดียว")
    st.stop()

files = tuple(sorted((u.name, u.getvalue()) for u in ups))
key = hashlib.md5(b"".join(n.encode() + hashlib.md5(d).digest() for n, d in files)).hexdigest()
if S.get("file_key") != key:
    for k in ("problems", "chosen", "result", "profs"):
        S.pop(k, None)
    S.file_key = key
    S.step = 2
if "profs" not in S:                       # เก็บใน session แทน cache เพื่อไม่ต้อง hash ไฟล์ใหญ่ทุกครั้งที่หน้ารีเฟรช
    # อ่านไฟล์ → จัดชนิดข้อมูล → คิดโจทย์ พร้อมแถบความคืบหน้า (ไฟล์ใหญ่หลายร้อย MB ใช้เวลาหลายสิบวินาที)
    bar = st.progress(0, text=f"0% · เริ่มประมวลผล {len(files)} ไฟล์ ({sum(len(d) for _, d in files) / 1e6:,.1f} MB)")
    steps, shown = load_steps(list(files)), -1
    try:
        while True:
            frac, label = next(steps)
            pct = min(int(frac * 100), 100)
            if pct != shown:
                bar.progress(pct, text=f"{pct}% · {label}")
                shown = pct
                pass  # ui-yield: เว็บ (stlite) แทนด้วย await asyncio.sleep(0) ให้หน้าจอวาดแถบใหม่ระหว่างประมวลผล
    except StopIteration as done:
        S.profs, S.problems = done.value
    except Exception as e:
        bar.empty()
        st.error(f"อ่านไฟล์ไม่ได้: {e}")
        st.stop()
    bar.progress(100, text="100% · ประมวลผลไฟล์เสร็จแล้ว")
profs = S.profs

for p in profs:
    with st.expander(f"{p.name}: {p.n:,} แถว · {len(p.cols)} คอลัมน์" + (f" · {p.df[p.date_col].min():%d/%m/%Y} – {p.df[p.date_col].max():%d/%m/%Y}" if p.date_col else ""), expanded=False):
        for n in p.notes:
            st.write("•", n)
        sens = sensitive_cols(p)
        if sens:
            st.warning(f"คอลัมน์ข้อมูลอ่อนไหว {len(sens)} คอลัมน์ (ชื่อ/ทะเบียน/บัญชี) — ใช้นับได้ แต่จะไม่แสดงบน dashboard: {', '.join(sens[:10])}")
        st.dataframe(p.df.drop(columns=[c for c in sens if c in p.df.columns]).head(20), width="stretch")

# ============================================================ 2. เลือกโจทย์
st.subheader("โจทย์ที่ระบบเสนอ")
if "problems" not in S:
    S.problems = propose_all(profs)
probs = S.problems
labels = []
for i, pr in enumerate(probs):
    labels.append(f"โจทย์ {i + 1} · น้ำหนัก {pr['weight']}% — {pr['title']}")
    esc = html.escape                           # ชื่อโจทย์มีชื่อคอลัมน์จากไฟล์ผู้ใช้ — escape ก่อนวางใน HTML
    st.markdown(f'<div class="prob"><b>โจทย์ {i + 1}</b> · <span style="color:#2F6FDE">น้ำหนัก {pr["weight"]}%</span> · '
                f'<span style="color:#56657E">{TYPES[pr["type"]]}</span><br>{esc(pr["title"])}<br><small style="color:#56657E">'
                f'คำถามตัดสินใจ: {esc(pr["question"])} · ข้อมูลบอกว่า: {esc(str(pr["why"]))}</small>'
                f'<div class="wbar" style="width:{pr["weight"]}%"></div></div>', unsafe_allow_html=True)
st.caption("น้ำหนัก = ผลกระทบต่อธุรกิจ × ความพร้อมของข้อมูล × ความชัดของสัญญาณในข้อมูล (รวม 100%) — ยิ่งสูงยิ่งควรทำก่อน"
           + (f" · ข้อมูลชุดนี้คิดโจทย์ที่มีหลักฐานรองรับได้ {len(probs)} ข้อ — กรอกโจทย์เองเพิ่มได้" if len(probs) < 5 else ""))

mode = st.radio("เลือกโจทย์", ["ใช้โจทย์ที่ระบบเสนอ", "กรอกโจทย์เอง"], horizontal=True)
chosen = None
if mode == "ใช้โจทย์ที่ระบบเสนอ":
    i = st.radio("โจทย์", range(len(probs)), format_func=lambda k: labels[k], label_visibility="collapsed")
    chosen = dict(probs[i])
    if chosen["type"] == CROSS:
        chosen = premium_settings(chosen)
else:
    q = st.text_area("พิมพ์โจทย์หรือคำถามที่อยากให้วิเคราะห์", placeholder="เช่น ปีไหน yield ดีที่สุด / ลูกค้ารายใหญ่กระจุกแค่ไหน / สินค้าออร์แกนิกขายแพงกว่าทั่วไปเท่าไหร่")
    cross = [c for c in probs if c["type"] == CROSS]
    types = [k for k in TYPES if k != CROSS or cross]           # โจทย์ข้ามไฟล์ใช้ได้เมื่อแนบไฟล์ซื้อ + ไฟล์ขาย
    # จัดประเภทด้วย Clef (decision model) ก่อน · เรียกไม่ได้ค่อยใช้กฎคำเดิม · เรียกครั้งเดียวต่อข้อความโจทย์
    clf = None
    if q and q.strip():
        if S.get("clf_q") != q.strip():
            with st.spinner("Clef กำลังจัดประเภทโจทย์…"):
                S.clf = classify(q.strip(), SITE.get("origin"))
            S.clf_q = q.strip()
        clf = S.clf
    if clf:
        ranked = sorted(types, key=lambda k: -clf["probabilities"].get(k, 0))   # ประเภทที่ใช้กับไฟล์ชุดนี้ไม่ได้ถูกข้ามไป
        g = ranked[0]
    else:
        g = guess_type(q or "")
    t = st.selectbox("วิธีวิเคราะห์ (ระบบเดาให้จากคำถาม แก้ได้)", types, index=types.index(g if g in types else "gap"), format_func=lambda k: TYPES[k])
    if clf:
        pr_ = clf["probabilities"]
        top = max(pr_, key=pr_.get)
        if top not in types:                                  # เช่น โจทย์ความคุ้มค่าซื้อ vs ขาย แต่แนบไฟล์มาชุดเดียว
            st.warning(f"Clef มองว่าโจทย์นี้เป็น '{TYPES.get(top, top)}' ({pr_[top]:.0%}) ซึ่งต้องแนบไฟล์ซื้อและไฟล์ขายคู่กัน "
                       "— แนบไฟล์เพิ่มแล้วระบบจะเสนอให้เอง หรือเลือกวิธีอื่นด้านบน")
        else:
            st.caption(f"Clef แนะนำ: {TYPES[ranked[0]]} ({pr_.get(ranked[0], 0):.0%}) · อันดับ 2: {TYPES[ranked[1]]} ({pr_.get(ranked[1], 0):.0%})"
                       " · ส่งเฉพาะข้อความโจทย์ไปจัดประเภท ไม่ส่งข้อมูลในไฟล์")
    elif q and q.strip():
        st.caption("เชื่อมต่อ Clef ไม่ได้ — ใช้กฎคำเดิมเดาประเภทแทน (ตรวจและแก้ได้)")
    if t == CROSS:
        chosen = premium_settings(dict(cross[0], title=q.strip(), question=q.strip(), weight=None)) if q else None
    else:
        di = st.selectbox("ไฟล์ที่ใช้", range(len(profs)), format_func=lambda k: profs[k].name) if len(profs) > 1 else 0
        p = profs[di]
        num = [c.name for c in p.of("number") if not c.sensitive]
        cat = [c.name for c in p.of("category") if not c.sensitive]
        dates = [c.name for c in p.of("date")]
        anycol = [c.name for c in p.cols.values() if c.nunique >= 2]
        # เรียงคอลัมน์ให้ตัวที่ตรงกับคำในโจทย์ขึ้นก่อน — โจทย์ "A ต่อ B" ให้ A เป็นตัวตั้ง B เป็นตัวหาร
        parts = re.split(r"ต่อ|/", q or "", maxsplit=1)
        head, tail = (parts[0], parts[1]) if len(parts) == 2 else (q or "", q or "")
        num_top, num_den = rank(num, head), rank(num, tail)
        if num_den and num_top and num_den[0] == num_top[0]:
            num_den = num_den[1:] + num_den[:1]
        num, cat, anycol = rank(num, q), rank(cat, q), rank(anycol, q)
        pick =lambda lbl, opts, idx=0, opt=False: st.selectbox(lbl, ([None] if opt else []) + opts, index=0 if opt else min(idx, max(len(opts) - 1, 0)),
                                                               format_func=lambda v: "— ไม่ใช้ —" if v is None else v)
        c1, c2, c3 = st.columns(3)
        params = {}
        if t != "quality" and not dates and t not in ("ratio", "concentration"):
            st.error("วิธีนี้ต้องมีคอลัมน์วันที่ในไฟล์")
        with c1:
            if t in ("ratio",):
                params["num"] = pick("ตัวตั้ง (เช่น ผลผลิต)", num_top)
            if t in ("trend", "threshold"):
                params["value"] = pick("ค่าที่ต้องการดู", num) if t == "threshold" else pick("ค่าที่ต้องการดู (ไม่เลือก = นับจำนวนรายการ)", num, opt=True)
            if t == "gap":
                params["group"] = pick("คอลัมน์กลุ่ม", cat)
            if t == "concentration":
                params["entity"] = pick("ราย (ลูกค้า/ผู้ขาย — ไม่แสดงชื่อ)", anycol)
            if t == "relation":
                params["x"] = pick("ตัวแปร X", num)
        with c2:
            if t == "ratio":
                params["den"] = pick("ตัวหาร (เช่น วัตถุดิบเข้า)", num_den)
            if t == "gap":
                params["amount"] = pick("ยอดเงิน", num)
            if t == "concentration":
                params["value"] = pick("ค่าที่ใช้วัด", num)
            if t == "relation":
                params["y"] = pick("ตัวแปร Y", num, 1)
            if t == "trend":
                params["by"] = pick("แยกตามกลุ่ม (ไม่บังคับ)", cat, opt=True)
            if t == "threshold":
                th = st.number_input("เกณฑ์ขั้นต่ำ (0 = ให้ระบบตั้ง)", value=0.0)
                params["threshold"] = th or None
        with c3:
            if t == "gap":
                params["qty"] = pick("ปริมาณ", num)
                params["site"] = pick("จุด/สาขา (ไม่บังคับ)", cat, opt=True)
            if dates and t != "quality":
                params["date"] = pick("วันที่", dates, dates.index(p.date_col) if p.date_col in dates else 0)
            elif t == "quality":
                params["date"] = p.date_col
        if q:
            chosen = dict(type=t, params=params, title=q.strip(), question=q.strip(), weight=None, ds=di)

go_btn = st.button("วิเคราะห์โจทย์นี้", type="primary", disabled=chosen is None)
if go_btn and chosen:
    with st.spinner("กำลังวิเคราะห์ตามหลัก: ถ่วงน้ำหนัก · ตัดผลฤดูกาล · เทียบวันเดียวกัน · ปิดข้อมูลอ่อนไหว…"):
        try:
            S.result = run_any(profs, chosen)
            S.chosen = chosen
            S.step = 3
        except Exception as e:
            st.error(str(e))

# ============================================================ 3. dashboard
if "result" in S:
    res = S.result
    st.divider()
    st.subheader("Dashboard สรุปผู้บริหาร")
    page = build_html(res, APP)
    st.iframe(page, height=2700)   # หน้าที่ระบบสร้างเอง (escape ข้อความแล้ว) ไม่ใช่ HTML จากผู้ใช้
    st.iframe(download_html(page, f"dashboard-{res['type']}.html"), height=52)

    # ======================================================== 4. LINE
    S.step = max(S.step, 4)
    st.divider()
    st.subheader("ข้อความส่ง LINE ถึงผู้บริหาร")
    msg = line_message(res, None)
    st.iframe(line_copy_html(msg))             # ความสูงปรับตามเนื้อหาเอง (มือถือที่ข้อความตัดหลายบรรทัดก็ไม่ล้น)
    st.caption(f"{len(msg)} ตัวอักษร · {msg.count(chr(10)) + 1} บรรทัด (ไม่เกิน {LINE_MAX_CHARS} ตัวอักษร ให้อ่านจบในแชตโดยไม่ต้องกด 'อ่านเพิ่มเติม') · "
               "กดปุ่ม คัดลอกข้อความ แล้ววางใน LINE · แนบไฟล์ dashboard ที่ดาวน์โหลดไปพร้อมกันได้")
