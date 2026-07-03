
import { getTunnelStatus, getTailscaleStatus, getDownloadStatus } from "../../../lib/tunnel/index.js";

export async function GET(req, res) {
  try {
    const [tunnel, tailscale] = await Promise.all([getTunnelStatus(), getTailscaleStatus()]);
    const download = getDownloadStatus();
    return res.json({ tunnel, tailscale, download });
  } catch (error) {
    console.error("Tunnel status error:", error);
    return res.status(500).json({ error: error.message });
  }
}
