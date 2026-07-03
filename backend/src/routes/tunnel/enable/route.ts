
import { enableTunnel } from "../../../lib/tunnel/index.js";

const DNS_WARMUP_DELAY_MS = 8000;

export async function POST(req, res) {
  try {
    const result = await enableTunnel();
    // Wait for DNS warmup to propagate at Cloudflare edge after tunnel registered
    await new Promise((r) => setTimeout(r, DNS_WARMUP_DELAY_MS));
    return res.json(result);
  } catch (error) {
    console.error("Tunnel enable error:", error);
    return res.status(500).json({ error: error.message });
  }
}
