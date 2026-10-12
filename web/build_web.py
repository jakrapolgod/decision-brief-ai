"""คัดลอกแอป Streamlit ไปไว้ใน web/public แล้วสร้าง index.html ที่รันแอปในเบราว์เซอร์ด้วย stlite

รัน:  python web/build_web.py
แล้ว deploy:  cd web && npx --yes wrangler@4.132.0 pages deploy --branch=main --commit-dirty=true
"""
import json
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUB = Path(__file__).resolve().parent / "public"
STLITE = "1.9.2"
REQUIREMENTS = ["pandas", "numpy", "openpyxl", "plotly"]

if PUB.exists():
    shutil.rmtree(PUB)
(PUB / "py" / "engine").mkdir(parents=True)
files = ["app.py"] + [f"engine/{p.name}" for p in sorted((ROOT / "engine").glob("*.py"))]
for f in files:
    shutil.copy2(ROOT / f, PUB / "py" / f)

# stlite รัน Python ใน thread เดียวกับที่ส่งหน้าจอ แถบความคืบหน้าจะขยับได้ต้องคืนจังหวะด้วย await
# (stlite รองรับ await ระดับบนสุดของสคริปต์ แต่ Streamlit ในเครื่องไม่รองรับ จึงแทนเฉพาะสำเนาบนเว็บ)
app = (PUB / "py" / "app.py").read_text(encoding="utf-8")
marker = "pass  # ui-yield"
assert marker in app, "ไม่พบจุด ui-yield ใน app.py"
app = app.replace(marker, "await asyncio.sleep(0)  # ui-yield").replace("import hashlib\n", "import asyncio\nimport hashlib\n", 1)
(PUB / "py" / "app.py").write_text(app, encoding="utf-8")

ver = time.strftime("%Y%m%d%H%M%S")          # กัน cache ไฟล์ .py เก่าหลัง deploy ใหม่
mount_files = {f: {"url": f"./py/{f}?v={ver}"} for f in files}

(PUB / "index.html").write_text(f"""<!doctype html>
<html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Decision Brief AI</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@stlite/browser@{STLITE}/build/stlite.css">
<style>
body{{margin:0;background:#fff;font-family:'IBM Plex Sans Thai',Sarabun,sans-serif}}
#boot{{position:fixed;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;color:#1B2A4A;background:#F4F6FA;z-index:10;padding:16px;text-align:center}}
#boot b{{font-size:1.3rem}} #boot small{{color:#56657E;max-width:420px}}
.spin{{width:34px;height:34px;border:4px solid #DCEBFF;border-top-color:#2F6FDE;border-radius:50%;animation:s 1s linear infinite}}
@keyframes s{{to{{transform:rotate(360deg)}}}}
</style></head><body>
<div id="boot"><div class="spin"></div><b>Decision Brief AI</b>
<small>กำลังเตรียมระบบวิเคราะห์ในเบราว์เซอร์ (ครั้งแรกประมาณ 20–40 วินาที ครั้งต่อไปเร็วขึ้น)<br>ไฟล์ที่แนบจะถูกวิเคราะห์ในเครื่องของคุณ ไม่ถูกส่งขึ้นเซิร์ฟเวอร์</small></div>
<div id="root"></div>
<script type="module">
import {{ mount }} from "https://cdn.jsdelivr.net/npm/@stlite/browser@{STLITE}/build/stlite.js";
const files = {json.dumps(mount_files)};
mount({{ requirements: {json.dumps(REQUIREMENTS)}, entrypoint: "app.py", files }}, document.getElementById("root"));
// ซ่อนหน้าโหลดเมื่อ Streamlit วาดหน้าแอปแล้ว
const t = setInterval(() => {{
  if (document.querySelector('[data-testid="stMain"]')) {{ document.getElementById("boot").remove(); clearInterval(t); }}
}}, 500);
</script></body></html>
""", encoding="utf-8")
print("built", PUB, "·", len(files), "python files · stlite", STLITE)
