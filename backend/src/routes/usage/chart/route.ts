
import { getChartData } from "../../../lib/usageDb.js";

const VALID_PERIODS = new Set(["today", "24h", "7d", "30d", "60d"]);

export async function GET_handler(req, res) {
  try {
    const { searchParams } = new URL('http://localhost' + req.originalUrl);
    const period = searchParams.get("period") || "7d";

    if (!VALID_PERIODS.has(period)) {
      return res.status(400).json({ error: "Invalid period" });
    }

    const data = await getChartData(period);
    return res.json(data);
  } catch (error) {
    console.error("[API] Failed to get chart data:", error);
    return res.status(500).json({ error: "Failed to fetch chart data" });
  }
}
