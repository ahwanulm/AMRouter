
import { getSettings } from "../../../../lib/localDb.js";
import { fetchOidcDiscovery, getPublicOrigin, probeOidcClientSecret } from "../../../../lib/auth/oidc.js";
import { verifyDashboardAuthToken } from "../../../../lib/auth/dashboardSession.js";

async function canAccessTestRoute() {
  const settings = await getSettings();
  if (settings.requireLogin === false) return true;

  const cookieStore = { get: (k) => ({ value: (req).cookies?.[k] }) };
  const token = cookieStore.get("auth_token")?.value;
  return await verifyDashboardAuthToken(token);
}

export async function POST_handler(req, res) {
  try {
    if (!(await canAccessTestRoute())) {
      return res.status(401).json({ error: "Unauthorized" });
    }

    const body = req.body.catch(() => ({}));
    const settings = await getSettings();

    const issuerUrl = String(body.issuerUrl || settings.oidcIssuerUrl || "").trim();
    const clientId = String(body.clientId || settings.oidcClientId || "").trim();
    const scopes = String(body.scopes || settings.oidcScopes || "openid profile email").trim() || "openid profile email";
    const clientSecret = String(
      Object.prototype.hasOwnProperty.call(body, "clientSecret")
        ? body.clientSecret
        : settings.oidcClientSecret || ""
    ).trim();

    if (!issuerUrl) {
      return res.status(400).json({ error: "Issuer URL is required" });
    }
    if (!clientId) {
      return res.status(400).json({ error: "Client ID is required" });
    }

    const discovery = await fetchOidcDiscovery(issuerUrl);
    const redirectUri = `${getPublicOrigin(request)}/api/auth/oidc/callback`;
    const secretProbe = await probeOidcClientSecret({
      tokenEndpoint: discovery.token_endpoint,
      clientId,
      clientSecret,
      redirectUri,
    });

    if (secretProbe.tested && secretProbe.valid === false) {
      return res.json({
        ok: false,
        discoveryOk: true,
        clientSecretTested: true,
        clientSecretValid: false,
        issuerUrl,
        clientId,
        scopes,
        redirectUri,
        authorizationEndpoint: discovery.authorization_endpoint || "",
        tokenEndpoint: discovery.token_endpoint || "",
        jwksUri: discovery.jwks_uri || "",
        error: `Discovery loaded, but the client secret is not valid: ${secretProbe.message}`,
      });
    }

    return res.json({
      ok: true,
      discoveryOk: true,
      clientSecretTested: secretProbe.tested,
      clientSecretValid: secretProbe.valid,
      issuerUrl,
      clientId,
      scopes,
      redirectUri,
      authorizationEndpoint: discovery.authorization_endpoint || "",
      tokenEndpoint: discovery.token_endpoint || "",
      jwksUri: discovery.jwks_uri || "",
      message: secretProbe.message,
    });
  } catch (error) {
    return res.status(500).json({ error: error.message || "OIDC test failed" });
  }
}
