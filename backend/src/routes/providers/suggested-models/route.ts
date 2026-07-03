
import { FILTERS } from "./filters.js";

export const dynamic = "force-dynamic";

export async function GET_handler(req, res) {
  const { searchParams } = new URL('http://localhost' + req.originalUrl);
  const url = searchParams.get("url");
  const type = searchParams.get("type");

  if (!url || !type) {
    return res.status(400).json({ error: "Missing url or type" });
  }

  const filter = FILTERS[type];
  if (!filter) {
    return res.status(400).json({ error: "Unknown filter type" });
  }

  try {
    const fetchRes = await fetch(url);
    if (!fetchRes.ok) {
      return res.json({ data: [] });
    }
    const json = await fetchRes.json();
    const raw = json.data ?? json.models ?? json;
    const data = filter(Array.isArray(raw) ? raw : []);
    return res.json({ data });
  } catch {
    return res.json({ data: [] });
  }
}
