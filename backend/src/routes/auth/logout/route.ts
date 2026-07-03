
import { clearDashboardAuthCookie } from "../../../lib/auth/dashboardSession.js";

export async function POST(req, res) {
  clearDashboardAuthCookie(res);
  res.clearCookie("oidc_state");
  res.clearCookie("oidc_nonce");
  res.clearCookie("oidc_code_verifier");
  return res.json({ success: true });
}
