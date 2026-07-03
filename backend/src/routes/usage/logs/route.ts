
import { getRecentLogs } from "../../../lib/usageDb.js";

export async function GET(req, res) {
  try {
    const logs = await getRecentLogs(200);
    return res.json(logs);
  } catch (error) {
    console.error("Error fetching logs:", error);
    return res.status(500).json({ error: "Failed to fetch logs" });
  }
}
