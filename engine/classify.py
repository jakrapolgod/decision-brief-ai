"""จัดประเภทโจทย์ที่ผู้ใช้พิมพ์เองด้วย decision model Clef ผ่าน /api/classify ของเว็บ (Cloudflare Workers AI)

ส่งเฉพาะข้อความโจทย์ ไม่ส่งข้อมูลในไฟล์ · ใช้ได้ทั้งบนเว็บ (stlite) และตอนรันในเครื่อง
ถ้าเรียกไม่ได้ (ออฟไลน์ / โมเดลล่ม) คืน None แล้วแอปกลับไปใช้กฎคำเดิม (guess_type)
"""
from __future__ import annotations

import json
import sys

SITE = "https://decision-brief-ai.pages.dev"


def classify(question: str, origin: str | None = None, timeout: int = 8) -> dict | None:
    url = f"{origin or SITE}/api/classify"
    body = json.dumps({"question": question[:500]}, ensure_ascii=False)
    try:
        if sys.platform == "emscripten":                     # stlite: XHR แบบ synchronous ใน web worker
            from js import XMLHttpRequest
            xhr = XMLHttpRequest.new()
            xhr.open("POST", url, False)
            xhr.setRequestHeader("Content-Type", "application/json")
            xhr.send(body)
            status, text = xhr.status, xhr.responseText
        else:
            import urllib.request
            req = urllib.request.Request(url, data=body.encode("utf-8"), method="POST",
                                         headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (Decision Brief AI)"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                status, text = r.status, r.read().decode("utf-8")
        out = json.loads(text or "{}")
        return out if status == 200 and isinstance(out.get("probabilities"), dict) else None
    except Exception:
        return None
