
import { disableTunnel } from "../../../lib/tunnel/index.js";

export async function POST(req, res) {
  try {
    const result = await disableTunnel();
    return res.json(result);
  } catch (error) {
    console.error("Tunnel disable error:", error);
    return res.status(500).json({ error: error.message });
  }
}
