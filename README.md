# Decision Brief AI

แนบไฟล์ข้อมูล → ระบบเสนอโจทย์ 5 ข้อพร้อมน้ำหนัก (หรือพิมพ์โจทย์เอง) → วิเคราะห์ → dashboard สรุปผู้บริหาร → ข้อความ LINE พร้อมปุ่มคัดลอก

Upload your data files → get 5 weighted decision questions (or type your own) → analysis → executive dashboard → a LINE-ready summary.

**ใช้งาน:** https://decision-brief-ai.pages.dev

## ความเป็นส่วนตัว / Privacy

- แอปรันในเบราว์เซอร์ทั้งหมดผ่าน [stlite](https://github.com/whitphx/stlite) — **ไฟล์ข้อมูลไม่ถูกส่งขึ้นเซิร์ฟเวอร์**
- โจทย์ที่พิมพ์เองจัดประเภทด้วยกฎคำในเบราว์เซอร์ — ไม่มีการส่งข้อความหรือข้อมูลออกไปไหน
- คอลัมน์ที่ดูเหมือนชื่อคน บริษัท ทะเบียนรถ หรือเลขบัญชี ใช้นับได้แต่ไม่แสดงบน dashboard

All processing happens in your browser; nothing you upload or type is sent anywhere.

## วิธีวิเคราะห์ / Analyses

อัตราส่วน (ถ่วงน้ำหนัก ตัดผลฤดูกาล) · แนวโน้มรายปี + ฤดูกาล · ส่วนต่างราคาระหว่างกลุ่ม (เทียบวันเดียวกัน) · การกระจุกตัวของลูกค้า/ผู้ขาย · ยอดคงเหลือต่ำกว่าเกณฑ์ · ความสัมพันธ์ 2 ตัวแปร · คุณภาพข้อมูล · ความคุ้มค่าส่วนเพิ่ม ซื้อ vs ขาย (ข้ามไฟล์)

หลักที่ใช้ทุกโจทย์: อัตราส่วนถ่วงน้ำหนักตามปริมาณ · เทียบปีโดยตัดผลฤดูกาล · ปีไม่ครบติดป้าย * · เทียบราคาวันเดียวกัน · ความสัมพันธ์ไม่ใช่เหตุและผล · ทุกผลมีข้อเสนอ / ไม่ทำ / trade-off / แผนลงมือพร้อม KPI / ข้อจำกัด

## รันในเครื่อง / Run locally

```bash
pip install streamlit pandas numpy openpyxl plotly
python -m streamlit run app.py --server.port 8600
```

## Deploy ขึ้น Cloudflare Pages ของคุณเอง

```bash
python web/build_web.py
cd web && npx --yes wrangler@4.132.0 pages deploy --branch=main --commit-dirty=true
```

แก้ `name` ใน `web/wrangler.toml` เป็นชื่อโปรเจกต์ของคุณ

`app.py` ต้องมีบรรทัด `pass  # ui-yield` — build จะแทนเป็น `await asyncio.sleep(0)` ให้แถบความคืบหน้าขยับบนเว็บ

## ทดสอบ / Tests

```bash
python tests/test_dates.py      # รูปแบบวันที่ (ISO, วัน/เดือน/ปี, พ.ศ.)
python tests/edge_cases.py      # ไฟล์แปลก ๆ + สุ่มโจทย์ + กัน XSS
```

## License

[MIT](LICENSE)
