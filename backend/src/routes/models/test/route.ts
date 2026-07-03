
import { pingModelByKind } from "./ping.js";

// POST /api/models/test - Ping a single model via internal completions or embeddings
export async function POST_handler(req, res) {
  try {
    const { model, kind } = req.body;
    if (!model) return res.status(400).json({ error: "Model required" });
    const result = await pingModelByKind(model, kind || "llm");
    return res.json(result);
  } catch (err) {
    return res.status(500).json({ ok: false, error: err.message });
  }
}
