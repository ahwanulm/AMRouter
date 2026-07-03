
import { enableTailscale } from "../../../lib/tunnel/index.js";

export async function POST(req, res) {
  try {
    const result = await enableTailscale();
    return res.json(result);
  } catch (error) {
    console.error("Tailscale enable error:", error.message);
    return res.status(500).json({ error: error.message });
  }
}
