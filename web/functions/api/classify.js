// POST /api/classify — จัดประเภทโจทย์ที่ผู้ใช้พิมพ์ ให้ตรงกับวิธีวิเคราะห์ของระบบ ด้วย decision model Clef (Workers AI)
// ส่งเฉพาะข้อความโจทย์ ไม่ส่งข้อมูลในไฟล์ · คำตอบจำกัดอยู่ใน 8 ประเภทนี้เท่านั้น จึงใช้เป็น AI ทั่วไปไม่ได้
// คำอธิบายแบบสั้นไม่มีตัวอย่าง · วัดความแม่นได้ด้วย tests/compare_classify.py
const TYPES = {
  ratio: "ratio / efficiency between two quantities",
  trend: "change of one measure over time and seasonality",
  gap: "price-per-unit difference between groups",
  concentration: "concentration of customers or suppliers",
  threshold: "balance or level falling below a minimum",
  relation: "relationship between two variables",
  quality: "data quality and completeness",
  premium_roi: "whether a purchase premium is covered by a sales premium",
};
const MODELS = { clef: "@cf/cloudflare/clef", "clef-flash": "@cf/cloudflare/clef-flash" };

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json; charset=utf-8" } });

export async function onRequestPost({ request, env }) {
  if (!env.AI) return json({ error: "ยังไม่ได้ผูก Workers AI" }, 503);
  // เรียกจากหน้าเว็บอื่นไม่ได้ (กันเว็บอื่นยืมใช้โควตา Workers AI ของบัญชีนี้)
  const origin = request.headers.get("origin");
  if (origin && origin !== new URL(request.url).origin) return json({ error: "forbidden" }, 403);
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "ข้อมูลไม่ถูกต้อง" }, 400);
  }
  const question = typeof body?.question === "string" ? body.question.trim().slice(0, 500) : "";
  const model = MODELS[body?.model] ? body.model : "clef";
  if (!question) return json({ error: "ไม่มีโจทย์" }, 400);
  const t0 = Date.now();
  try {
    const out = await env.AI.run(MODELS[model], {
      model,
      state: { question, context: "ธุรกิจหรือโรงงาน ผู้ใช้แนบไฟล์ข้อมูล (ยอดขาย การผลิต การรับซื้อ การเงิน เครื่องจักร) แล้วพิมพ์โจทย์ที่ต้องการให้วิเคราะห์" },
      questions: {
        analysis_type: { type: "choice", instructions: "โจทย์นี้ควรวิเคราะห์ด้วยวิธีไหน (เลือกวิธีที่ตอบคำถามตัดสินใจได้ตรงที่สุด)", criteria: TYPES },
      },
    });
    const a = out?.answers?.analysis_type;
    if (!a) return json({ error: "โมเดลไม่ตอบ" }, 502);
    return json({ type: a.choice, confidence: a.confidence, probabilities: a.probabilities, model, ms: Date.now() - t0, usage: out.usage });
  } catch (e) {
    return json({ error: `เรียกโมเดลไม่สำเร็จ: ${e?.message || e}` }, 502);
  }
}
