
import { getSettings } from "../../../lib/localDb.js";

export async function GET(req, res) {
  try {
    const settings = await getSettings();
    const requireLogin = settings.requireLogin !== false;
    const tunnelDashboardAccess = settings.tunnelDashboardAccess !== false;
    const tunnelUrl = settings.tunnelUrl || "";
    const tailscaleUrl = settings.tailscaleUrl || "";
    return res.json({ requireLogin, tunnelDashboardAccess, tunnelUrl, tailscaleUrl });
  } catch (error) {
    return res.status(200).json({ requireLogin: true });
  }
}
