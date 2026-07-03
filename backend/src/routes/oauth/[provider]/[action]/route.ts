
import { 
  getProvider, 
  generateAuthData, 
  exchangeTokens, 
  requestDeviceCode, 
  pollForToken 
} from "../../../../lib/oauth/providers.js";
import { createProviderConnection } from "../../../../models/index.js";
import {
  startCodexProxy,
  stopCodexProxy,
  registerCodexSession,
  getCodexSessionStatus,
  clearCodexSession,
  startXaiProxy,
  stopXaiProxy,
  registerXaiSession,
  getXaiSessionStatus,
  clearXaiSession,
  startAntigravityProxy,
  stopAntigravityProxy,
  registerAntigravitySession,
  getAntigravitySessionStatus,
  clearAntigravitySession,
} from "../../../../lib/oauth/utils/server.js";

async function completeXaiManualCode(code, state) {
  const session = state ? getXaiSessionStatus(state) : null;
  if (!session) {
    throw new Error("xAI OAuth session not found; restart the login flow and paste the code again");
  }
  if (!code) throw new Error("Missing xAI authorization code");

  try {
    const tokenData = await exchangeTokens(
      "xai",
      code,
      session.redirectUri,
      session.codeVerifier,
      state
    );
    const connection = await createProviderConnection({
      provider: "xai",
      authType: "oauth",
      ...tokenData,
      expiresAt: tokenData.expiresIn
        ? new Date(Date.now() + tokenData.expiresIn * 1000).toISOString()
        : null,
      testStatus: "active",
    });
    clearXaiSession(state);
    stopXaiProxy();
    return {
      id: connection.id,
      provider: connection.provider,
      email: connection.email,
      displayName: connection.displayName,
    };
  } catch (err) {
    clearXaiSession(state);
    stopXaiProxy();
    throw err;
  }
}

/**
 * Dynamic OAuth API Route
 * Handles: authorize, exchange, device-code, poll
 */

// GET /api/oauth/[provider]/authorize - Generate auth URL
// GET /api/oauth/[provider]/device-code - Request device code (for device_code flow)
export async function GET_handler(req, res, { params }) {
  try {
    const { provider, action } = await params;
    const { searchParams } = new URL('http://localhost' + req.originalUrl);

    if (action === "authorize") {
      const redirectUri = searchParams.get("redirect_uri") || "http://localhost:8080/callback";
      // Collect provider-specific meta params (e.g. gitlab passes baseUrl, clientId, clientSecret)
      const reservedParams = new Set(["redirect_uri"]);
      const meta = {};
      searchParams.forEach((value, key) => { if (!reservedParams.has(key)) meta[key] = value; });
      const authData = await generateAuthData(provider, redirectUri, Object.keys(meta).length ? meta : undefined);
      return res.json(authData);
    }

    if (action === "start-proxy") {
      if (!["codex", "xai", "antigravity"].includes(provider)) {
        return res.status(400).json({ error: "Proxy only supported for codex/xai/antigravity" });
      }
      const appPort = searchParams.get("app_port");
      if (!appPort) {
        return res.status(400).json({ error: "Missing app_port" });
      }
      const state = searchParams.get("state");
      const codeVerifier = searchParams.get("code_verifier");
      const redirectUri = searchParams.get("redirect_uri");
      const result = provider === "xai"
        ? await startXaiProxy(Number(appPort))
        : provider === "antigravity"
        ? await startAntigravityProxy(Number(appPort))
        : await startCodexProxy(Number(appPort));
      let serverSide = false;
      if (result.success && state && redirectUri) {
        if (provider === "xai" && codeVerifier) {
          serverSide = registerXaiSession({ state, codeVerifier, redirectUri });
        } else if (provider === "antigravity") {
          serverSide = registerAntigravitySession({ state, redirectUri });
        } else if (provider === "codex" && codeVerifier) {
          serverSide = registerCodexSession({ state, codeVerifier, redirectUri });
        }
      }
      return res.json({ ...result, serverSide });
    }

    if (action === "poll-status") {
      if (!["codex", "xai", "antigravity"].includes(provider)) {
        return res.status(400).json({ error: "Poll only supported for codex/xai/antigravity" });
      }
      const state = searchParams.get("state");
      if (!state) {
        return res.status(400).json({ error: "Missing state" });
      }
      const session = provider === "xai"
        ? getXaiSessionStatus(state)
        : provider === "antigravity"
        ? getAntigravitySessionStatus(state)
        : getCodexSessionStatus(state);
      if (!session) return res.json({ status: "unknown" });
      if (session.status === "done" || session.status === "error") {
        const payload = { ...session };
        if (provider === "xai") clearXaiSession(state);
        else if (provider === "antigravity") clearAntigravitySession(state);
        else clearCodexSession(state);
        return res.json(payload);
      }
      return res.json({ status: session.status });
    }

    if (action === "stop-proxy") {
      if (!["codex", "xai", "antigravity"].includes(provider)) {
        return res.status(400).json({ error: "Proxy only supported for codex/xai/antigravity" });
      }
      if (provider === "xai") stopXaiProxy();
      else if (provider === "antigravity") stopAntigravityProxy();
      else stopCodexProxy();
      return res.json({ success: true });
    }

    if (action === "device-code") {
      const providerData = getProvider(provider);
      if (providerData.flowType !== "device_code") {
        return res.status(400).json({ error: "Provider does not support device code flow" });
      }

      const authData = await generateAuthData(provider, null);
      const startUrl = searchParams.get("start_url");
      const region = searchParams.get("region");
      const authMethod = searchParams.get("auth_method");
      const deviceOptions = provider === "kiro"
        ? {
            ...(startUrl ? { startUrl } : {}),
            ...(region ? { region } : {}),
            ...(authMethod ? { authMethod } : {}),
          }
        : undefined;
      
      // Providers that don't use PKCE for device code
      const noPkceDeviceProviders = ["github", "kiro", "kimi-coding", "kilocode", "codebuddy", "qoder"];
      let deviceData;
      if (noPkceDeviceProviders.includes(provider)) {
        deviceData = await requestDeviceCode(provider, undefined, deviceOptions);
      } else {
        // Qwen and other PKCE providers
        deviceData = await requestDeviceCode(provider, authData.codeChallenge, deviceOptions);
      }

      return res.json({
        ...deviceData,
        // Prefer the verifier the provider's requestDeviceCode generated for
        // itself (qoder rolls its own PKCE pair); fall back to the generic one.
        codeVerifier: deviceData.codeVerifier || authData.codeVerifier,
      });
    }

    return res.status(400).json({ error: "Unknown action" });
  } catch (error) {
    console.log("OAuth GET error:", error);
    return res.status(500).json({ error: error.message });
  }
}

// POST /api/oauth/[provider]/exchange - Exchange code for tokens and save
// POST /api/oauth/[provider]/poll - Poll for token (device_code flow)
export async function POST_handler(req, res, { params }) {
  try {
    const { provider, action } = await params;
    let body;
    try {
      body = req.body;
    } catch {
      return res.status(400).json({ error: "Invalid or empty request body" });
    }

    if (action === "exchange") {
      const { code, redirectUri, codeVerifier, state, meta } = body;

      // Detect if "code" is actually a raw JWT access token (starts with eyJ)
      if (code && code.startsWith("eyJ") && code.includes(".")) {
        const { extractCodexAccountInfo } = await import("../../../../lib/oauth/providers");
        const info = extractCodexAccountInfo(code);

        // Also decode JWT directly for ChatGPT website tokens which use
        // top-level account_id/plan_type instead of nested openai auth claims
        let directPayload = {};
        try {
          const b64 = code.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
          const padded = b64 + "=".repeat((4 - b64.length % 4) % 4);
          directPayload = JSON.parse(Buffer.from(padded, "base64").toString("utf8"));
        } catch {}

        const accountId = info.chatgptAccountId || directPayload.account_id;
        const planType = info.chatgptPlanType || directPayload.plan_type;
        const email = info.email || directPayload.email;

        const providerSpecificData = { authMethod: "access_token" };
        if (accountId) providerSpecificData.chatgptAccountId = accountId;
        if (planType) providerSpecificData.chatgptPlanType = planType;

        const connection = await createProviderConnection({
          provider,
          authType: "access_token",
          accessToken: code,
          email: email || null,
          providerSpecificData,
          testStatus: "active",
        });

        return res.json({
          success: true,
          connection: {
            id: connection.id,
            provider: connection.provider,
            email: connection.email,
            displayName: connection.displayName,
          }
        });
      }

      // Cline uses authorization_code without PKCE
      const noPkceExchangeProviders = ["cline"];
      if (!code || !redirectUri || (!codeVerifier && !noPkceExchangeProviders.includes(provider))) {
        return res.status(400).json({ error: "Missing required fields" });
      }

      // Exchange code for tokens (meta carries provider-specific params, e.g. gitlab clientId/baseUrl)
      const tokenData = await exchangeTokens(provider, code, redirectUri, codeVerifier, state, meta);

      // Save to database
      const connection = await createProviderConnection({
        provider,
        authType: "oauth",
        ...tokenData,
        expiresAt: tokenData.expiresIn 
          ? new Date(Date.now() + tokenData.expiresIn * 1000).toISOString() 
          : null,
        testStatus: "active",
      });

      return res.json({ 
        success: true, 
        connection: {
          id: connection.id,
          provider: connection.provider,
          email: connection.email,
          displayName: connection.displayName,
        }
      });
    }

    if (action === "poll") {
      const { deviceCode, codeVerifier, extraData } = body;

      if (!deviceCode) {
        return res.status(400).json({ error: "Missing device code" });
      }

      // Providers that don't use PKCE for device code
      const noPkceProviders = ["github", "kimi-coding", "kilocode", "codebuddy"];
      let result;
      if (noPkceProviders.includes(provider)) {
        result = await pollForToken(provider, deviceCode);
      } else if (provider === "kiro") {
        // Kiro needs extraData (clientId, clientSecret) from device code response
        result = await pollForToken(provider, deviceCode, null, extraData);
      } else if (provider === "qoder") {
        // Qoder needs both the PKCE verifier (codeVerifier) and the machineId
        // captured at device-code time (extraData._qoderMachineId) so
        // mapTokens can persist it for COSY signing.
        if (!codeVerifier) {
          return res.status(400).json({ error: "Missing code verifier" });
        }
        result = await pollForToken(provider, deviceCode, codeVerifier, extraData);
      } else {
        // Qwen and other PKCE providers
        if (!codeVerifier) {
          return res.status(400).json({ error: "Missing code verifier" });
        }
        result = await pollForToken(provider, deviceCode, codeVerifier);
      }

      if (result.success) {
        // Save to database
        const connection = await createProviderConnection({
          provider,
          authType: "oauth",
          ...result.tokens,
          expiresAt: result.tokens.expiresIn 
            ? new Date(Date.now() + result.tokens.expiresIn * 1000).toISOString() 
            : null,
          testStatus: "active",
        });

        return res.json({ 
          success: true, 
          connection: {
            id: connection.id,
            provider: connection.provider,
          }
        });
      }

      // Still pending or error - don't create connection for pending states
      const isPending = result.pending || result.error === "authorization_pending" || result.error === "slow_down";
      
      return res.json({
        success: false,
        error: result.error,
        errorDescription: result.errorDescription,
        pending: isPending,
      });
    }

    if (action === "manual-code") {
      if (provider !== "xai") {
        return res.status(400).json({ error: "Manual code only supported for xai" });
      }
      const { code, state } = body;
      const connection = await completeXaiManualCode(String(code || "").trim(), String(state || "").trim());
      return res.json({ success: true, connection });
    }

    return res.status(400).json({ error: "Unknown action" });
  } catch (error) {
    console.log("OAuth POST error:", error);
    return res.status(500).json({ error: error.message });
  }
}
