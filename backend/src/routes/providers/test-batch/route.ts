
import { getProviderConnections } from "../../../models/index.js";
import {
  FREE_PROVIDERS,
  OAUTH_PROVIDERS,
  APIKEY_PROVIDERS,
  OPENAI_COMPATIBLE_PREFIX,
  ANTHROPIC_COMPATIBLE_PREFIX,
} from "../../../shared/constants/providers.js";
import { testSingleConnection } from "../[id]/test/testUtils.js";

function getAuthGroup(providerId, connection = null) {
  // Prioritize authType from connection if available
  if (connection?.authType) {
    if (connection.authType === "oauth") {
      // Check if it's a free provider
      if (FREE_PROVIDERS[providerId]) return "free";
      return "oauth";
    }
    return connection.authType;
  }
  
  // Fallback to constants
  if (FREE_PROVIDERS[providerId]) return "free";
  if (OAUTH_PROVIDERS[providerId]) return "oauth";
  if (APIKEY_PROVIDERS[providerId]) return "apikey";
  if (
    typeof providerId === "string" &&
    (providerId.startsWith(OPENAI_COMPATIBLE_PREFIX) || providerId.startsWith(ANTHROPIC_COMPATIBLE_PREFIX))
  )
    return "compatible";
  return "apikey";
}

function isCompatibleProvider(providerId) {
  return (
    typeof providerId === "string" &&
    (providerId.startsWith(OPENAI_COMPATIBLE_PREFIX) || providerId.startsWith(ANTHROPIC_COMPATIBLE_PREFIX))
  );
}

// POST /api/providers/test-batch - Test multiple connections by group
export async function POST_handler(req, res) {
  try {
    const body = req.body;
    const { mode, providerId } = body;

    if (!mode) {
      return res.status(400).json({ error: "mode is required" });
    }

    const allConnections = await getProviderConnections({ isActive: true });

    let connectionsToTest = [];
    if (mode === "provider" && providerId) {
      connectionsToTest = allConnections.filter((c) => c.provider === providerId);
    } else if (mode === "oauth") {
      connectionsToTest = allConnections.filter((c) => getAuthGroup(c.provider, c) === "oauth");
    } else if (mode === "free") {
      connectionsToTest = allConnections.filter((c) => getAuthGroup(c.provider, c) === "free");
    } else if (mode === "apikey") {
      connectionsToTest = allConnections.filter((c) => getAuthGroup(c.provider, c) === "apikey");
    } else if (mode === "compatible") {
      connectionsToTest = allConnections.filter((c) => isCompatibleProvider(c.provider));
    } else if (mode === "all") {
      connectionsToTest = allConnections;
    } else {
      return res.status(400).json(
        { error: "Invalid mode. Use: provider, oauth, free, apikey, compatible, all" }
      );
    }

    if (connectionsToTest.length === 0) {
      return res.json({
        mode,
        providerId: providerId || null,
        results: [],
        summary: { total: 0, passed: 0, failed: 0 },
        testedAt: new Date().toISOString(),
      });
    }

    const results = [];
    for (const conn of connectionsToTest) {
      try {
        const data = await testSingleConnection(conn.id);
        results.push({
          provider: conn.provider,
          connectionId: conn.id,
          connectionName: conn.name || conn.email || conn.provider,
          authType: conn.authType || getAuthGroup(conn.provider, conn),
          valid: data.valid,
          latencyMs: data.latencyMs || 0,
          error: data.error || null,
          diagnosis: data.diagnosis || null,
          statusCode: data.statusCode || null,
          testedAt: data.testedAt || new Date().toISOString(),
        });
      } catch (error) {
        results.push({
          provider: conn.provider,
          connectionId: conn.id,
          connectionName: conn.name || conn.email || conn.provider,
          authType: conn.authType || getAuthGroup(conn.provider, conn),
          valid: false,
          latencyMs: 0,
          error: error.message,
          diagnosis: { type: "network_error", source: "local", code: null, message: error.message },
          statusCode: null,
          testedAt: new Date().toISOString(),
        });
      }
    }

    return res.json({
      mode,
      providerId: providerId || null,
      results,
      testedAt: new Date().toISOString(),
      summary: {
        total: results.length,
        passed: results.filter((r) => r.valid).length,
        failed: results.filter((r) => !r.valid).length,
      },
    });
  } catch (error) {
    console.log("Error in batch test:", error);
    return res.status(500).json({ error: "Batch test failed" });
  }
}
