
import { getSettings } from "../../../lib/localDb.js";
import { isOidcConfigured } from "../../../lib/auth/oidc.js";
import { getDashboardAuthSession } from "../../../lib/auth/dashboardSession.js";

export async function GET(req, res) {
  try {
    const settings = await getSettings();
    const cookieStore = { get: (k) => ({ value: (req).cookies?.[k] }) };
    const session = await getDashboardAuthSession(cookieStore.get("9r_session")?.value);
    const requireLogin = settings.requireLogin !== false;
    const authMode = settings.authMode || "password";
    const oidcName = String(session?.oidcName || "").trim();
    const oidcEmail = String(session?.oidcEmail || "").trim();
    const displayName = oidcName || oidcEmail || (session?.oidc ? "OIDC user" : "Password user");
    const loginMethod = session?.oidc ? "OIDC" : "Password";

    return res.json({
      requireLogin,
      authMode,
      oidcConfigured: isOidcConfigured(settings),
      oidcLoginLabel: (settings.oidcLoginLabel || "Sign in with OIDC").trim() || "Sign in with OIDC",
      hasPassword: !!settings.password,
      displayName,
      loginMethod,
      oidcName: oidcName || null,
      oidcEmail: oidcEmail || null,
      oidcLogin: !!session?.oidc,
      isLoggedIn: !!session,
    });
  } catch {
    return res.json({
      requireLogin: true,
      authMode: "password",
      oidcConfigured: false,
      oidcLoginLabel: "Sign in with OIDC",
      hasPassword: false,
      displayName: "Password user",
      loginMethod: "Password",
      oidcName: null,
      oidcEmail: null,
      oidcLogin: false,
      isLoggedIn: false,
    });
  }
}
