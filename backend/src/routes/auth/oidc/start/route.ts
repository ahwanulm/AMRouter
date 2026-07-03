
import {
  buildOidcAuthorizationUrl,
  createOidcNonce,
  createOidcState,
  createPkcePair,
  fetchOidcDiscovery,
  getOidcRuntimeConfig,
  getPublicOrigin,
} from "../../../../lib/auth/oidc.js";
import { shouldUseSecureCookie } from "../../../../lib/auth/dashboardSession.js";

export async function GET_handler(req, res) {
  try {
    const config = await getOidcRuntimeConfig();
    if (!config) {
      return res.redirect(new URL("/login?error=oidc_not_configured", getPublicOrigin(request)));
    }

    const discovery = await fetchOidcDiscovery(config.issuerUrl);
    const state = createOidcState();
    const nonce = createOidcNonce();
    const { verifier, challenge } = createPkcePair();
    const redirectUri = `${getPublicOrigin(request)}/api/auth/oidc/callback`;
    const authUrl = buildOidcAuthorizationUrl({
      authorizationEndpoint: discovery.authorization_endpoint,
      clientId: config.clientId,
      redirectUri,
      scopes: config.scopes,
      state,
      nonce,
      codeChallenge: challenge,
    });

    const cookieStore = { get: (k) => ({ value: (req).cookies?.[k] }) };
    const baseOptions = {
      httpOnly: true,
      secure: shouldUseSecureCookie(request),
      sameSite: "lax",
      path: "/",
      maxAge: 10 * 60,
    };
    cookieStore.set("oidc_state", state, baseOptions);
    cookieStore.set("oidc_nonce", nonce, baseOptions);
    cookieStore.set("oidc_code_verifier", verifier, baseOptions);

    return res.redirect(authUrl);
  } catch (error) {
    return res.redirect(new URL(`/login?error=${encodeURIComponent(error.message || "oidc_start_failed")}`, getPublicOrigin(request)));
  }
}
