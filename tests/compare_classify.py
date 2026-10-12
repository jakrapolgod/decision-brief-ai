"""เทียบการจัดประเภทโจทย์: กฎเดิม (guess_type) vs decision model Clef-flash / Clef vs แบบผสม

รัน: 1) cd web && npx --yes wrangler@4.132.0 pages dev --port 8788
     2) python tests/compare_classify.py [--base http://localhost:8788]
แบบผสม = ใช้คำตอบ Clef เมื่อ confidence ≥ เกณฑ์ ไม่งั้นใช้กฎเดิม
"""
import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine.analysis import guess_type  # noqa: E402

BASE = sys.argv[sys.argv.index("--base") + 1] if "--base" in sys.argv else "http://localhost:8788"
CASES = json.loads((Path(__file__).with_name("classify_cases.json")).read_text(encoding="utf-8"))
MODELS = ["clef-flash", "clef"]
THRESHOLDS = {"_p": [0.4, 0.5, 0.6, 0.7], "_conf": [0.2, 0.3, 0.4, 0.5]}   # ความน่าจะเป็นของคำตอบ / confidence ของ Clef


def clef(q, model):
    req = urllib.request.Request(f"{BASE}/api/classify", data=json.dumps({"question": q, "model": model, "plain": "--plain" in sys.argv}).encode(),
                                 headers={"content-type": "application/json", "User-Agent": "Mozilla/5.0 decision-brief-test"})
    t = time.time()
    r = json.loads(urllib.request.urlopen(req, timeout=60).read())
    r["rtt"] = (time.time() - t) * 1000
    return r


rows = []
for c in CASES:
    row = dict(q=c["q"], ok=c["ok"])
    t = time.perf_counter()
    row["rule"] = guess_type(c["q"])
    row["rule_ms"] = (time.perf_counter() - t) * 1000
    for m in MODELS:
        r = clef(c["q"], m)
        row[m] = r["type"]
        row[m + "_conf"] = r["confidence"]
        row[m + "_p"] = r["probabilities"][r["type"]]
        row[m + "_ms"] = r["ms"]
        row[m + "_tok"] = r["usage"]["input_tokens"]
    rows.append(row)
    print(f"{'✓' if row['rule'] in c['ok'] else '✗'}rule {row['rule']:13} "
          + " ".join(f"{'✓' if row[m] in c['ok'] else '✗'}{m} {row[m]:13} ({row[m + '_conf']:.2f})" for m in MODELS)
          + f"  | {c['q'][:50]}")

n = len(rows)
acc = lambda key: sum(r[key] in r["ok"] for r in rows) / n
print(f"\nจำนวนโจทย์ {n} ข้อ")
print(f"กฎเดิม       ถูก {acc('rule'):.0%} · เวลาเฉลี่ย {statistics.mean(r['rule_ms'] for r in rows):.3f} ms")
for m in MODELS:
    print(f"{m:12} ถูก {acc(m):.0%} · เวลาโมเดลเฉลี่ย {statistics.mean(r[m + '_ms'] for r in rows):.0f} ms "
          f"(P50 {statistics.median(r[m + '_ms'] for r in rows):.0f}) · โทเคนเฉลี่ย {statistics.mean(r[m + '_tok'] for r in rows):.0f}")
    for key, ths in THRESHOLDS.items():
        for th in ths:
            hyb = sum((r[m] if r[m + key] >= th else r["rule"]) in r["ok"] for r in rows) / n
            used = sum(r[m + key] >= th for r in rows)
            print(f"   ผสม (เชื่อ {m} เมื่อ {'prob' if key == '_p' else 'conf'} ≥ {th}) ถูก {hyb:.0%} · ใช้คำตอบโมเดล {used}/{n} ข้อ")
    right = [r[m + "_conf"] for r in rows if r[m] in r["ok"]]
    wrong = [r[m + "_conf"] for r in rows if r[m] not in r["ok"]]
    print(f"   confidence เฉลี่ย ตอนตอบถูก {statistics.mean(right):.2f} · ตอนตอบผิด {statistics.mean(wrong) if wrong else float('nan'):.2f}")
by_type = {}
for r in rows:
    k = r["ok"][0]
    by_type.setdefault(k, []).append(r)
print("\nแยกตามประเภท (ถูก/ทั้งหมด): ประเภท | กฎเดิม | " + " | ".join(MODELS))
for k, rs in by_type.items():
    print(f"  {k:13} | {sum(r['rule'] in r['ok'] for r in rs)}/{len(rs)} | "
          + " | ".join(f"{sum(r[m] in r['ok'] for r in rs)}/{len(rs)}" for m in MODELS))
Path(__file__).with_name("compare_classify_result" + ("_plain" if "--plain" in sys.argv else "") + ".json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
