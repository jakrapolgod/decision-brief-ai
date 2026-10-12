"""สร้างหน้า dashboard สรุปผู้บริหาร (HTML ไฟล์เดียว) · ข้อความ LINE

หน้า dashboard เป็น HTML นิ่ง + Plotly — เปิดบนมือถือได้ทันที ไม่ต้องรอโหลด Python
มีเฉพาะตัวเลขสรุปและข้อมูลกราฟที่รวมกลุ่มแล้ว ไม่มีข้อมูลดิบ ชื่อคน/บริษัท ทะเบียน หรือเลขบัญชี
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re

import plotly.io as pio


def e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def js(obj) -> str:
    """JSON สำหรับฝังใน <script> — กัน '</script>' ในชื่อคอลัมน์/ข้อความจากไฟล์ผู้ใช้ปิดแท็กแล้วรันโค้ดแปลกปลอม"""
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/").replace("<!--", "<\\!--")


def build_html(res: dict, app_name: str = "Decision Brief AI") -> str:
    figs = [json.loads(pio.to_json(c["fig"], validate=False)) for c in res["charts"]]
    charts_html = "".join(
        f'<div class="chart"><h3>{e(c["title"])}</h3><p class="cq">{e(c["sub"])}</p><div class="plot" id="g{i}"></div></div>'
        for i, c in enumerate(res["charts"]))
    kpis = "".join(f'<div class="kpi"><div class="kl">{e(k[0])}</div><div class="kv">{e(k[1])}</div><div class="ks">{e(k[2])}</div></div>'
                   for k in res["kpis"])
    steps = "".join(f'<li><span class="n">{i}</span><div><b>{e(a[0])}</b><br><small>ผู้รับผิดชอบ: {e(a[1])} (เสนอ) · {e(a[2])} · KPI: {e(a[3])}</small></div></li>'
                    for i, a in enumerate(res["actions"], 1))
    limits = "".join(f"<li>{e(x)}</li>" for x in res["limits"] + ["หน้านี้มีเฉพาะตัวเลขสรุป ไม่มีข้อมูลดิบ ชื่อบุคคล/บริษัท ทะเบียน หรือเลขบัญชี"])
    table = res["table"].to_html(classes="tbl", border=0, float_format=lambda v: f"{v:,.2f}") if res.get("table") is not None else ""
    today = dt.date.today().strftime("%d/%m/") + str(dt.date.today().year + 543)
    return f"""<!doctype html>
<html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(res['title'])}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Thai:wght@400;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"></script>
<style>
:root{{--bg:#F4F6FA;--ink:#1B2A4A;--muted:#56657E;--line:#E3E8F0;--blue:#2F6FDE;--ok:#2E9E57;--amber:#F2A516;--violet:#7A5AF0}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font-family:'IBM Plex Sans Thai',Sarabun,sans-serif;font-size:15px;line-height:1.55}}
.wrap{{max-width:1180px;margin:0 auto;padding:18px 16px 40px}}
.brand{{font-size:.8rem;letter-spacing:.06em;color:var(--blue);font-weight:700;text-transform:uppercase}}
h1{{font-size:1.6rem;margin:.1rem 0 .2rem}} .sub{{color:var(--muted);font-size:.88rem}}
.band{{display:grid;grid-template-columns:150px 1fr;gap:16px;border:1px solid var(--line);border-radius:16px;padding:14px 18px;margin-top:10px;background:#fff}}
.band>.lab{{font-weight:700;font-size:1rem;display:flex;align-items:flex-start;gap:8px}}
.dot{{width:10px;height:10px;border-radius:50%;margin-top:7px;flex-shrink:0}}
.b-dq{{background:#EAF2FE}} .b-ins{{background:#EAF7EE}} .b-rec{{background:#FFF6E3}} .b-act{{background:#F2EFFC}}
.q{{font-size:1.2rem;font-weight:700;margin:0 0 .3rem}} .ans{{display:inline-block;background:#fff;border:1px solid #C9DBFA;border-radius:10px;padding:7px 12px}}
.kpis{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}}
.kpi{{border:1px solid var(--line);border-radius:12px;padding:10px 14px}} .kl{{font-weight:600;color:#3A4760;font-size:.92rem}}
.kv{{font-size:1.9rem;font-weight:700;line-height:1.25}} .ks{{color:var(--muted);font-size:.86rem}}
.charts{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px 22px}}
.chart h3{{font-size:1rem;margin:0}} .cq{{color:var(--muted);font-size:.86rem;margin:.1rem 0 .2rem}} .plot{{width:100%;min-height:320px}}
.headline{{font-size:1.05rem;margin:0 0 .3rem}} .muted{{color:var(--muted);font-size:.9rem}}
ol.steps{{list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:9px}} ol.steps li{{display:flex;gap:10px}}
.n{{width:26px;height:26px;border-radius:50%;background:var(--violet);color:#fff;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0;font-size:.85rem}}
details{{background:#fff;border:1px solid var(--line);border-radius:12px;padding:10px 16px;margin-top:10px}} summary{{cursor:pointer;font-weight:600}}
.tbl{{border-collapse:collapse;width:100%;font-size:.86rem;margin-top:8px}} .tbl th,.tbl td{{border-bottom:1px solid var(--line);padding:5px 8px;text-align:right}}
.tbl th:first-child,.tbl td:first-child{{text-align:left}} .scroll{{overflow-x:auto}}
footer{{color:var(--muted);font-size:.8rem;margin-top:14px}}
@media (max-width:820px){{.band{{grid-template-columns:1fr;gap:6px}} .kpis,.charts{{grid-template-columns:1fr}} h1{{font-size:1.3rem}}}}
</style></head><body><div class="wrap">
<div class="brand">{e(app_name)}</div>
<h1>{e(res['title'])}</h1>
<div class="sub">ข้อมูล {e(res.get('source_note', ''))} · สร้างเมื่อ {today}</div>
<section class="band b-dq"><div class="lab"><span class="dot" style="background:var(--blue)"></span>คำถามที่ต้องตัดสินใจ</div><div>
<p class="q">{e(res['question'])}</p><div class="ans"><b>คำตอบสั้น:</b> {e(res['answer'])}</div></div></section>
<section class="band"><div class="lab"><span class="dot" style="background:var(--blue)"></span>ตัวชี้วัด (KPI)</div><div class="kpis">{kpis}</div></section>
<section class="band"><div class="lab"><span class="dot" style="background:var(--blue)"></span>หลักฐาน</div><div class="charts">{charts_html}</div></section>
<section class="band b-ins"><div class="lab"><span class="dot" style="background:var(--ok)"></span>ข้อค้นพบ</div><div>
<p class="headline">{e(res['insight'])}</p><div class="muted"><b>สมมติฐานที่ยังต้องพิสูจน์:</b> {e(res.get('hypothesis', ''))}</div></div></section>
<section class="band b-rec"><div class="lab"><span class="dot" style="background:var(--amber)"></span>ข้อเสนอ</div><div>
<p class="headline"><b>ทำ:</b> {e(res['do'])}</p><div class="muted"><b>ไม่ทำ:</b> {e(res.get('not_do', ''))} · <b>Trade-off:</b> {e(res.get('tradeoff', ''))}</div></div></section>
<section class="band b-act"><div class="lab"><span class="dot" style="background:var(--violet)"></span>แผนลงมือ</div><div><ol class="steps">{steps}</ol></div></section>
<details><summary>ตารางตัวเลขสรุป</summary><div class="scroll">{table}</div></details>
<details><summary>ข้อจำกัดของข้อมูล</summary><ol>{limits}</ol></details>
<footer>สร้างโดย {e(app_name)} · วิเคราะห์จากไฟล์ของผู้ใช้ในเบราว์เซอร์ หน้านี้มีเฉพาะตัวเลขสรุป</footer>
</div>
<script>
const F = {js(figs)};
F.forEach((f, i) => Plotly.newPlot('g' + i, f.data, Object.assign({{}}, f.layout, {{autosize: true}}), {{displayModeBar: false, responsive: true}}));
</script></body></html>"""


LINE_MAX_CHARS = 480        # LINE Official Account ส่งได้ 500 ตัวอักษร/กล่อง — เผื่อไว้ไม่ให้แชตพับข้อความ
LINE_MAX_LINES = 12


def _clip(s, n: int) -> str:
    s = re.sub(r"\s+", " ", str(s)).strip()
    if len(s) <= n:
        return s
    cut = s[:n - 1]
    sp = cut.rfind(" ")                      # ตัดที่ช่องว่างถ้ามี จะได้ไม่ขาดกลางคำ
    return (cut[:sp] if sp > n * .6 else cut).rstrip(" ·,—-") + "…"


def line_message(res: dict, url: str | None = None, max_chars: int = LINE_MAX_CHARS, max_lines: int = LINE_MAX_LINES) -> str:
    """ข้อความ LINE ถึงผู้บริหาร: คำตอบ + ตัวเลขสำคัญ + ข้อค้นพบ + ข้อเสนอ + ขั้นแรกที่ต้องทำ
    ยาวที่สุดที่ยังไม่เกิน max_chars ตัวอักษร / max_lines บรรทัด (ไม่ต้องกด 'อ่านเพิ่มเติม')
    แต่ละบรรทัดเก็บทั้งประโยค — ถ้าไม่พอที่ ให้ข้ามบรรทัดที่สำคัญน้อยแทนการตัดกลางประโยค"""
    title = res.get("short_title") or re.split(r"\s+—\s+", res["title"])[0]
    act = res.get("actions") or []
    # (ลำดับที่แสดง, ข้อความ, ความยาวสูงสุด, ลำดับความสำคัญ — 0 = ต้องมี, เลขน้อย = ใส่ก่อน)
    parts = [(0, "เรียนผู้บริหาร", 20, 0), (1, f"เรื่อง: {title}", 70, 0)]
    if url:
        parts.append((2, f"📊 {url}", 200, 0))
    parts.append((3, f"📌 {res['answer']}", 200, 0))
    for i, k in enumerate(res.get("kpis", [])[:3]):
        label, value, sub = (list(k) + ["", "", ""])[:3]
        parts.append((4 + i, f"• {label}: {value}" + (f" — {sub}" if sub else ""), 95, (1, 2, 5)[i]))
    do = res.get("do") or ""
    reco = do if len(do) <= 120 or not res.get("line_reco") else res["line_reco"]   # ข้อเสนอยาวมาก → ใช้ฉบับสั้น จะได้มีที่ให้ตัวเลข
    parts.append((8, f"➡️ {reco}", 160, 0))
    if res.get("insight"):
        parts.append((7, f"🔎 {res['insight']}", 150, 4))
    if act:
        what, owner, when = (list(act[0]) + ["", "", ""])[:3]
        parts.append((9, f"✅ ขั้นแรก: {what} ({owner} · {when})", 120, 3))

    lines = {o: _clip(t, n) for o, t, n, pr in parts if pr == 0}
    size = lambda: sum(len(x) for x in lines.values()) + len(lines) - 1
    for o, t, n, pr in sorted((p for p in parts if p[3]), key=lambda p: p[3]):
        line = _clip(t, n)
        if size() + 1 + len(line) <= max_chars and len(lines) < max_lines:
            lines[o] = line
    msg = "\n".join(lines[o] for o in sorted(lines))
    return msg if len(msg) <= max_chars else _clip(msg, max_chars)


def download_html(page: str, filename: str) -> str:
    """ปุ่มดาวน์โหลดหน้า dashboard ที่สร้างไฟล์ในเบราว์เซอร์เอง (ใช้ใน st.iframe)
    แทน st.download_button ซึ่งบนเว็บ (stlite) ไปดึงไฟล์จากเซิร์ฟเวอร์ Streamlit ที่ไม่มีอยู่จริงจึงดาวน์โหลดไม่ได้"""
    return f"""<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Thai:wght@600&display=swap" rel="stylesheet">
<style>body{{margin:0;background:transparent}}
button{{display:inline-flex;align-items:center;gap:8px;border:1px solid #C9DBFA;border-radius:10px;padding:9px 16px;cursor:pointer;
  font:600 14px 'IBM Plex Sans Thai',sans-serif;background:#fff;color:#1B2A4A}} button:hover{{background:#EAF2FE}}
svg{{width:17px;height:17px}}</style></head><body>
<button id="d" title="{e(filename)}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v12m0 0l-5-5m5 5l5-5M4 19h16"/></svg>
<span>ดาวน์โหลดหน้า dashboard (HTML)</span></button>
<script>
const PAGE = {js(page)}, NAME = {js(filename)};
document.getElementById('d').addEventListener('click', () => {{
  const url = URL.createObjectURL(new Blob([PAGE], {{type: 'text/html;charset=utf-8'}}));
  const a = document.createElement('a'); a.href = url; a.download = NAME; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}});
</script></body></html>"""


def line_copy_html(msg: str) -> str:
    """กล่องข้อความแบบแชต LINE + ปุ่มไอคอนคัดลอก (ใช้ใน st.iframe)"""
    return f"""<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Thai:wght@400;600&display=swap" rel="stylesheet">
<style>
body{{margin:0;font-family:'IBM Plex Sans Thai',Sarabun,sans-serif;background:transparent}}
.wrap{{background:#8CABD9;border-radius:14px;padding:14px 14px 12px;position:relative}}
.bubble{{background:#fff;border-radius:16px;padding:12px 14px;white-space:pre-wrap;line-height:1.55;font-size:15px;color:#1B2A4A;max-width:560px}}
.bar{{display:flex;justify-content:flex-end;gap:8px;margin-top:10px}}
button{{display:inline-flex;align-items:center;gap:6px;border:0;border-radius:999px;padding:8px 16px;font:600 14px 'IBM Plex Sans Thai',sans-serif;
  background:#06C755;color:#fff;cursor:pointer}}
button:hover{{background:#05b04b}} button.ok{{background:#1B2A4A}}
svg{{width:16px;height:16px}}
</style></head><body>
<div class="wrap"><div class="bubble" id="m">{html.escape(msg)}</div>
<div class="bar"><button id="b" title="คัดลอกข้อความไปวางใน LINE">
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
<span>คัดลอกข้อความ</span></button></div></div>
<script>
const MSG = {js(msg)};
const b = document.getElementById('b');
function done(ok) {{
  b.querySelector('span').textContent = ok ? 'คัดลอกแล้ว ✓' : 'คัดลอกไม่ได้ — เลือกข้อความแล้วกด Ctrl+C';
  b.classList.toggle('ok', ok);
  setTimeout(() => {{ b.querySelector('span').textContent = 'คัดลอกข้อความ'; b.classList.remove('ok'); }}, 2500);
}}
function fallback() {{                       // iframe บางแบบไม่อนุญาต clipboard API → ใช้วิธีเลือกข้อความแล้วคัดลอก
  const t = document.createElement('textarea'); t.value = MSG; t.style.position = 'fixed'; t.style.opacity = '0';
  document.body.appendChild(t); t.select();
  let ok = false; try {{ ok = document.execCommand('copy'); }} catch (e) {{}}
  t.remove(); done(ok);
}}
b.addEventListener('click', () => {{
  if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(MSG).then(() => done(true), fallback);
  else fallback();
}});
</script></body></html>"""
