
import { getUsageStats } from "../../../lib/usageDb.js";

export async function GET(req, res) {
  try {
    const stats = await getUsageStats();
    return res.json(stats);
  } catch (error) {
    console.error("Error fetching usage stats:", error);
    return res.status(500).json({ error: "Failed to fetch usage stats" });
  }
}
