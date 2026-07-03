
import {
  getProviderConnections,
  createProviderConnection,
  getProviderNodeById,
  getProviderNodes,
  getProxyPoolById,
} from "../../models/index.js";
import { APIKEY_PROVIDERS } from "../../shared/constants/config.js";
import { AI_PROVIDERS, FREE_TIER_PROVIDERS, WEB_COOKIE_PROVIDERS, isOpenAICompatibleProvider, isAnthropicCompatibleProvider, isCustomEmbeddingProvider } from "../../shared/constants/providers.js";
import { normalizeProviderId, normalizeProviderSpecificData } from "../../lib/providerNormalization.js";

export const dynamic = "force-dynamic";

function normalizeProxyConfig(body = {}) {
  const enabled = body?.connectionProxyEnabled === true;
  const url = typeof body?.connectionProxyUrl === "string" ? body.connectionProxyUrl.trim() : "";
  const noProxy = typeof body?.connectionNoProxy === "string" ? body.connectionNoProxy.trim() : "";

  if (enabled && !url) {
    return { error: "Connection proxy URL is required when connection proxy is enabled" };
  }

  return {
    connectionProxyEnabled: enabled,
    connectionProxyUrl: url,
    connectionNoProxy: noProxy,
  };
}

async function normalizeProxyPoolId(proxyPoolId) {
  if (proxyPoolId === undefined || proxyPoolId === null || proxyPoolId === "" || proxyPoolId === "__none__") {
    return { proxyPoolId: null };
  }

  const normalizedId = String(proxyPoolId).trim();
  if (!normalizedId) {
    return { proxyPoolId: null };
  }

  const proxyPool = await getProxyPoolById(normalizedId);
  if (!proxyPool) {
    return { error: "Proxy pool not found" };
  }

  return { proxyPoolId: normalizedId };
}

// GET /api/providers - List all connections
export async function GET(req, res) {
  try {
    const connections = await getProviderConnections();

    // Build nodeNameMap for compatible providers (id → name)
    let nodeNameMap = {};
    try {
      const nodes = await getProviderNodes();
      for (const node of nodes) {
        if (node.id && node.name) nodeNameMap[node.id] = node.name;
      }
    } catch { }

    // Hide sensitive fields, enrich name for compatible providers
    const safeConnections = connections.map(c => {
      const isCompatible = isOpenAICompatibleProvider(c.provider) || isAnthropicCompatibleProvider(c.provider);
      const name = isCompatible
        ? (c.name || nodeNameMap[c.provider] || c.providerSpecificData?.nodeName || c.provider)
        : c.name;
      return {
        ...c,
        name,
        apiKey: undefined,
        accessToken: undefined,
        refreshToken: undefined,
        idToken: undefined,
      };
    });

    return res.json({ connections: safeConnections });
  } catch (error) {
    console.log("Error fetching providers:", error);
    return res.status(500).json({ error: "Failed to fetch providers" });
  }
}

// POST /api/providers - Create new connection (API Key only, OAuth via separate flow)
export async function POST_handler(req, res) {
  try {
    const body = req.body;
    const provider = normalizeProviderId(body.provider);
    const { apiKey, email, name, displayName, priority, globalPriority, defaultModel, testStatus } = body;
    const proxyConfig = normalizeProxyConfig(body);
    if (proxyConfig.error) {
      return res.status(400).json({ error: proxyConfig.error });
    }

    const proxyPoolResult = await normalizeProxyPoolId(body.proxyPoolId);
    if (proxyPoolResult.error) {
      return res.status(400).json({ error: proxyPoolResult.error });
    }
    const proxyPoolId = proxyPoolResult.proxyPoolId;

    // Validation
    const isWebCookieProvider = !!WEB_COOKIE_PROVIDERS[provider];
    const isValidProvider = APIKEY_PROVIDERS[provider] ||
      FREE_TIER_PROVIDERS[provider] ||
      isWebCookieProvider ||
      isOpenAICompatibleProvider(provider) ||
      isAnthropicCompatibleProvider(provider) ||
      isCustomEmbeddingProvider(provider) ||
      provider === "codebuddy";

    if (!provider || !isValidProvider) {
      return res.status(400).json({ error: "Invalid provider" });
    }
    if (!apiKey && provider !== "ollama-local") {
      return res.status(400).json({ error: `${isWebCookieProvider ? "Cookie value" : "API Key"} is required` });
    }
    const connectionName = name || displayName || AI_PROVIDERS[provider]?.name;
    if (!connectionName) {
      return res.status(400).json({ error: "Name is required" });
    }

    let providerSpecificData = normalizeProviderSpecificData(provider, body, body.providerSpecificData);

    // Compatible/embedding nodes allow exactly one connection each. These guards were
    // dropped accidentally during the bun:sqlite refactor (v0.4.28); restored to honor
    // the contract locked in by tests/unit/compatible-provider-connections.test.js (#925).
    if (isOpenAICompatibleProvider(provider)) {
      const node = await getProviderNodeById(provider);
      if (!node) {
        return res.status(404).json({ error: "OpenAI Compatible node not found" });
      }
      const existingConnections = await getProviderConnections({ provider });
      if (existingConnections.length > 0) {
        return res.status(400).json({ error: "Only one connection is allowed for this OpenAI Compatible node" });
      }
      providerSpecificData = {
        prefix: node.prefix,
        apiType: node.apiType,
        baseUrl: node.baseUrl,
        nodeName: node.name,
      };
    } else if (isAnthropicCompatibleProvider(provider)) {
      const node = await getProviderNodeById(provider);
      if (!node) {
        return res.status(404).json({ error: "Anthropic Compatible node not found" });
      }
      const existingConnections = await getProviderConnections({ provider });
      if (existingConnections.length > 0) {
        return res.status(400).json({ error: "Only one connection is allowed for this Anthropic Compatible node" });
      }
      providerSpecificData = {
        prefix: node.prefix,
        baseUrl: node.baseUrl,
        nodeName: node.name,
      };
    } else if (isCustomEmbeddingProvider(provider)) {
      const node = await getProviderNodeById(provider);
      if (!node) {
        return res.status(404).json({ error: "Custom Embedding node not found" });
      }
      const existingConnections = await getProviderConnections({ provider });
      if (existingConnections.length > 0) {
        return res.status(400).json({ error: "Only one connection is allowed for this Custom Embedding node" });
      }
      providerSpecificData = {
        prefix: node.prefix,
        baseUrl: node.baseUrl,
        nodeName: node.name,
      };
    }

    const mergedProviderSpecificData = {
      ...(providerSpecificData || {}),
      connectionProxyEnabled: proxyConfig.connectionProxyEnabled,
      connectionProxyUrl: proxyConfig.connectionProxyUrl,
      connectionNoProxy: proxyConfig.connectionNoProxy,
    };

    if (proxyPoolId !== null) {
      mergedProviderSpecificData.proxyPoolId = proxyPoolId;
    }

    const newConnection = await createProviderConnection({
      provider,
      authType: isWebCookieProvider ? "cookie" : "apikey",
      name: connectionName,
      apiKey: apiKey || "",
      email: email || "",
      priority: priority || 1,
      globalPriority: globalPriority || null,
      defaultModel: defaultModel || null,
      providerSpecificData: mergedProviderSpecificData,
      isActive: true,
      testStatus: testStatus || "unknown",
    });

    // Hide sensitive fields
    const result = { ...newConnection };
    delete result.apiKey;

    return res.status(201).json({ connection: result });
  } catch (error) {
    console.log("Error creating provider:", error);
    return res.status(500).json({ error: "Failed to create provider" });
  }
}
