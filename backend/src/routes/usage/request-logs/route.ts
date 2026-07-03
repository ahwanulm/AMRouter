
import { getRecentLogs } from "../../../lib/usageDb.js";

export async function GET(req, res) {
  try {
    const logs = await getRecentLogs(200);
    return res.json(logs);
  } catch (error) {
    console.error("[API ERROR] /api/usage/logs failed:", error);
    console.error("[API ERROR] Stack:", error?.stack);
    return res.status(500).json({ error: "Failed to fetch logs" });
  }
}
