"""ทดสอบไฟล์แปลก ๆ ก่อนใช้จริง: ทุกไฟล์ต้อง (ก) ผ่านทั้งสาย หรือ (ข) แจ้งข้อความภาษาไทยที่เข้าใจได้ — ห้ามล้มแบบ traceback

รัน:  python tests/edge_cases.py
ทุกกรณีวิ่ง: อ่านไฟล์ + จัดชนิด + คิดโจทย์ (load_steps) → วิเคราะห์ทุกโจทย์ → dashboard HTML → ข้อความ LINE
แล้วสุ่มโจทย์ "กรอกเอง" ทุกวิธีวิเคราะห์กับคอลัมน์ต่าง ๆ (ต้องผ่าน หรือได้ ValueError ที่อธิบายได้)
"""
import io
import random
import sys
import traceback
import warnings
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine.analysis import TYPES  # noqa: E402
from engine.crossfile import TYPE as CROSS, load_steps, run_any  # noqa: E402
from engine.data_io import run_steps  # noqa: E402
from engine.publish import LINE_MAX_CHARS, build_html, line_copy_html, line_message  # noqa: E402

rng = np.random.default_rng(7)
random.seed(7)


def csv(df, enc="utf-8-sig", sep=","):
    return df.to_csv(index=False, sep=sep).encode(enc)


def xlsx(sheets: dict):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


days = pd.date_range("2022-01-01", "2024-12-31", freq="D")
base = pd.DataFrame({"วันที่": days, "ปริมาณ (ตัน)": rng.uniform(50, 150, len(days)).round(2),
                     "ยอดเงิน": rng.uniform(1e5, 9e5, len(days)).round(2), "กลุ่ม": rng.choice(["A", "B", "C"], len(days))})
CASES = {
    "ไฟล์ว่าง": [("empty.csv", b"")],
    "มีแต่หัวตาราง": [("head.csv", "วันที่,ปริมาณ\n".encode())],
    "แถวเดียว": [("one.csv", "วันที่,ปริมาณ,ยอดเงิน\n2024-01-01,10,100\n".encode())],
    "คอลัมน์เดียว": [("col.csv", csv(base[["ปริมาณ (ตัน)"]]))],
    "ไม่มีตัวเลข": [("text.csv", csv(pd.DataFrame({"ชื่อสินค้า": ["ก", "ข", "ค"] * 50, "หมายเหตุ": ["x"] * 150})))],
    "ไม่มีวันที่": [("nodate.csv", csv(base.drop(columns=["วันที่"])))],
    "ไฟล์ xls รุ่นเก่า": [("old.xls", b"\xd0\xcf\x11\xe0" + b"0" * 100)],
    "ไฟล์เสีย (xlsx ปลอม)": [("bad.xlsx", b"not a zip")],
    "ภาษาไทย cp874 + TAB": [("tis.txt", csv(base.head(400), enc="cp874", sep="\t"))],
    "UTF-16": [("u16.csv", csv(base.head(300), enc="utf-16"))],
    "ปี พ.ศ. + ชื่อเดือนไทยในชีต 2567": [("be.xlsx", xlsx({"2567": [["เดือน", "ผลิต (ตัน)", "รับเข้า (ตัน)"]] +
                                                            [[m, float(rng.uniform(80, 120)), float(rng.uniform(400, 600))] for m in
                                                             ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน"]]}))],
    "ค่าติดลบ ศูนย์ ว่าง ปนข้อความ": [("dirty.csv", csv(base.head(500).assign(
        **{"ปริมาณ (ตัน)": lambda d: d["ปริมาณ (ตัน)"].where(rng.random(len(d)) > .1, -5).astype(object).where(rng.random(len(d)) > .05, "n/a"),
           "ยอดเงิน": lambda d: d["ยอดเงิน"].where(rng.random(len(d)) > .1, 0)})))],
    "ตัวเลขมีจุลภาค": [("comma.csv", csv(base.head(300).assign(ยอดเงิน=lambda d: d["ยอดเงิน"].map(lambda v: f"{v:,.2f}"))))],
    "หัวคอลัมน์ซ้ำ": [("dup.csv", "วันที่,ปริมาณ,ปริมาณ\n2024-01-01,1,2\n2024-01-02,3,4\n2024-01-03,5,6\n".encode())],
    "ชื่อคอลัมน์มีสคริปต์ (XSS)": [("xss.csv", csv(base.rename(columns={"กลุ่ม": "<script>alert(1)</script>",
                                                                       "ปริมาณ (ตัน)": "ปริมาณ</script><img src=x onerror=alert(2)>"})))],
    "ข้อมูลอ่อนไหว (ชื่อ ทะเบียน บัญชี)": [("pii.csv", csv(base.head(600).assign(
        ผู้ขาย=[f"นาย สมชาย {i}" for i in range(600)], ทะเบียน=[f"กข {1000 + i}" for i in range(600)],
        **{"บัญชี 123-4-56789-0": rng.uniform(1e6, 2e6, 600)})))],
    "วันที่มีเวลา + แต่ละสาขาขายวันละชนิด": [("timed.csv", csv(pd.DataFrame({
        "วันเวลา": [d + pd.Timedelta(hours=int(h)) for d in days[:600] for h in (8, 12, 16)],
        "สาขา": ["ก", "ข", "ค"] * 600, "สินค้า": rng.choice(["ทั่วไป", "ออร์แกนิก"], 1800, p=[.7, .3]),
        "จำนวน (ชิ้น)": rng.integers(50, 150, 1800)}).assign(**{"ยอดขาย (บาท)": lambda x: x["จำนวน (ชิ้น)"] * np.where(x["สินค้า"] == "ออร์แกนิก", 120, 100)})))],
    "ไฟล์เดียวกันแนบซ้ำ 2 ครั้ง": [("a.csv", csv(base)), ("a_copy.csv", csv(base))],
    "สองไฟล์ไม่เกี่ยวกัน": [("a.csv", csv(base)), ("b.csv", csv(pd.DataFrame({"รหัส": range(100), "ค่า": rng.uniform(0, 1, 100)})))],
    "แนบ 6 ไฟล์ปนกัน": [(f"y{y}.csv", csv(base[base["วันที่"].dt.year == y])) for y in (2022, 2023, 2024)] +
                     [("c.csv", csv(base.head(10))), ("d.csv", b""), ("e.csv", csv(base[["ยอดเงิน"]]))],
}

ok = bad = 0
fail = []
for name, files in CASES.items():
    try:
        profs, probs = run_steps(load_steps(files))
    except ValueError as e:                  # แจ้งผู้ใช้ได้ (แอปแสดง "อ่านไฟล์ไม่ได้: …")
        print(f"  แจ้งเตือน  {name}: {str(e)[:90]}")
        ok += 1
        continue
    except Exception:
        bad += 1
        fail.append((name, "load", traceback.format_exc(limit=3)))
        print(f"✗ ล้ม      {name} (อ่าน/จัดชนิด)")
        continue
    errs = []
    for pr in probs:
        try:
            res = run_any(profs, pr)
            page = build_html(res)
            msg = line_message(res)
            assert len(msg) <= LINE_MAX_CHARS, f"LINE ยาว {len(msg)}"
            line_copy_html(msg)
            if "xss" in name.lower() or "XSS" in name:
                assert "alert(1)</script>" not in page, "ชื่อคอลัมน์ปิดแท็ก script ใน dashboard ได้"
                assert "</script><img" not in page, "ชื่อคอลัมน์หลุดออกจากแท็ก script ใน dashboard"
                assert "</script><img" not in line_copy_html(msg), "ข้อความหลุดออกจากแท็ก script ในปุ่มคัดลอก"
        except ValueError as e:
            errs.append(f"{pr['type']}: {str(e)[:60]}")
        except Exception:
            fail.append((name, pr["type"], traceback.format_exc(limit=4)))
            errs.append(f"✗ {pr['type']} ล้ม")
    # สุ่มโจทย์กรอกเอง: ทุกวิธี × คอลัมน์สุ่ม
    fuzz_bad = 0
    for di, p in enumerate(profs):
        nums = [c.name for c in p.of("number")] or [None]
        cats = [c.name for c in p.of("category")] + ["_sheet"] * 0 or [None]
        anyc = list(p.cols) or [None]
        for t in [k for k in TYPES if k != CROSS]:
            for _ in range(3):
                a, b2 = random.choice(nums), random.choice(nums)
                prm = {"ratio": dict(num=a, den=b2), "trend": dict(value=random.choice(nums + [None]), by=random.choice(cats + [None])),
                       "gap": dict(group=random.choice(cats), amount=a, qty=b2, site=random.choice(cats + [None])),
                       "concentration": dict(entity=random.choice(anyc), value=a), "threshold": dict(value=a, threshold=random.choice([None, 0.0, 1e9])),
                       "relation": dict(x=a, y=b2), "quality": {}}[t]
                prm["date"] = p.date_col
                try:
                    res = run_any(profs, dict(type=t, params=prm, title="โจทย์ทดสอบ", question="โจทย์ทดสอบ", ds=di))
                    build_html(res)
                    line_message(res)
                except ValueError:
                    pass
                except Exception:
                    fuzz_bad += 1
                    fail.append((name, f"กรอกเอง {t} {prm}", traceback.format_exc(limit=4)))
    if errs or fuzz_bad:                     # โจทย์ที่ระบบ "เสนอเอง" ต้องวิเคราะห์ได้ทุกข้อ — วิเคราะห์ไม่ได้ถือว่าไม่ผ่าน
        bad += 1
        print(f"✗ ไม่ผ่าน   {name}: {errs} · กรอกเองล้ม {fuzz_bad}")
    else:
        ok += 1
        print(f"✓ ผ่าน     {name}: {len(profs)} ชุด · {len(probs)} โจทย์" + (f" · แจ้งเหตุผล {len(errs)}" if errs else ""))

print(f"\nผ่าน/แจ้งเตือนได้ {ok} · ล้ม {bad}")
for n, where, tb in fail[:12]:
    print(f"\n--- {n} / {where}\n{tb[-900:]}")
sys.exit(1 if bad else 0)
