
import fs from "fs";
import {
  getProviderConnectionById,
  getProxyPoolById,
  updateProviderConnection,
  deleteProviderConnection,
} from "../../../models/index.js";
import { listCodeBuddyAccounts, deleteCodeBuddyAccount } from "../../../lib/db/index.js";


function normalizeProxyConfig(body = {}) {
  const hasAnyProxyField =
    Object.prototype.hasOwnProperty.call(body, "connectionProxyEnabled") ||
    Object.prototype.hasOwnProperty.call(body, "connectionProxyUrl") ||
    Object.prototype.hasOwnProperty.call(body, "connectionNoProxy");

  if (!hasAnyProxyField) return { hasAnyProxyField: false };

  const enabled = body?.connectionProxyEnabled === true;
  const url = typeof body?.connectionProxyUrl === "string" ? body.connectionProxyUrl.trim() : "";
  const noProxy = typeof body?.connectionNoProxy === "string" ? body.connectionNoProxy.trim() : "";

  if (enabled && !url) {
    return {
      hasAnyProxyField: true,
      error: "Connection proxy URL is required when connection proxy is enabled",
    };
  }

  return {
    hasAnyProxyField: true,
    connectionProxyEnabled: enabled,
    connectionProxyUrl: url,
    connectionNoProxy: noProxy,
  };
}

async function normalizeProxyPoolUpdate(proxyPoolIdInput) {
  if (proxyPoolIdInput === undefined) {
    return { hasProxyPoolField: false, proxyPoolId: null };
  }

  if (proxyPoolIdInput === null || proxyPoolIdInput === "" || proxyPoolIdInput === "__none__") {
    return { hasProxyPoolField: true, proxyPoolId: null };
  }

  const proxyPoolId = String(proxyPoolIdInput).trim();
  if (!proxyPoolId) {
    return { hasProxyPoolField: true, proxyPoolId: null };
  }

  const proxyPool = await getProxyPoolById(proxyPoolId);
  if (!proxyPool) {
    return { hasProxyPoolField: true, error: "Proxy pool not found" };
  }

  return { hasProxyPoolField: true, proxyPoolId };
}

function shouldMergeProviderSpecificData(existing, incoming, hasLegacyProxy, hasProxyPoolField) {
  return existing !== undefined || incoming !== undefined || hasLegacyProxy || hasProxyPoolField;
}

// GET /api/providers/[id] - Get single connection
export async function GET_handler(req, res, { params }) {
  try {
    const { id } = await params;
    const connection = await getProviderConnectionById(id);

    if (!connection) {
      return res.status(404).json({ error: "Connection not found" });
    }

    // If ?copy_secret=1 — return the secret value for clipboard copy
    const url = new URL('http://localhost' + req.originalUrl);
    if (url.searchParams.get("copy_secret") === "1") {
      const format = url.searchParams.get("format") || "json";
      if (connection.authType === "cookie" && connection.cookie && format === "json") {
        const cookiesList = [];
        const pieces = connection.cookie.split(";");
        for (let piece of pieces) {
          piece = piece.trim();
          if (!piece || !piece.includes("=")) continue;
          const eqIdx = piece.indexOf("=");
          const name = piece.slice(0, eqIdx).trim();
          const value = piece.slice(eqIdx + 1).trim();
          if (!name) continue;
          const secure = name.startsWith("__Secure-") || name.startsWith("__Host-");
          
          let domain = ".leonardo.ai";
          if (connection.provider === "canva") {
            domain = ".canva.com";
          }
          
          cookiesList.push({
            name,
            value,
            domain,
            path: "/",
            secure: secure,
            httpOnly: true,
            sameSite: "lax"
          });
        }
        const secret = JSON.stringify(cookiesList, null, 2);
        return res.json({ secret });
      }

      const secret = connection.cookie || connection.accessToken || connection.apiKey || "";
      return res.json({ secret });
    }

    // Hide sensitive fields
    const result = { ...connection };
    delete result.apiKey;
    delete result.accessToken;
    delete result.refreshToken;
    delete result.idToken;

    return res.json({ connection: result });
  } catch (error) {
    console.log("Error fetching connection:", error);
    return res.status(500).json({ error: "Failed to fetch connection" });
  }
}

// PUT /api/providers/[id] - Update connection
export async function PUT_handler(req, res, { params }) {
  try {
    const { id } = await params;
    const body = req.body;
    const {
      name,
      priority,
      globalPriority,
      defaultModel,
      isActive,
      apiKey,
      testStatus,
      lastError,
      lastErrorAt,
      providerSpecificData
    } = body;

    const existing = await getProviderConnectionById(id);
    if (!existing) {
      return res.status(404).json({ error: "Connection not found" });
    }

    const proxyConfig = normalizeProxyConfig(body);
    if (proxyConfig.error) {
      return res.status(400).json({ error: proxyConfig.error });
    }

    const proxyPoolResult = await normalizeProxyPoolUpdate(body.proxyPoolId);
    if (proxyPoolResult.error) {
      return res.status(400).json({ error: proxyPoolResult.error });
    }

    const updateData = {};
    if (name !== undefined) updateData.name = name;
    if (priority !== undefined) updateData.priority = priority;
    if (globalPriority !== undefined) updateData.globalPriority = globalPriority;
    if (defaultModel !== undefined) updateData.defaultModel = defaultModel;
    if (isActive !== undefined) updateData.isActive = isActive;
    if (apiKey && existing.authType === "apikey") updateData.apiKey = apiKey;
    if (testStatus !== undefined) updateData.testStatus = testStatus;
    if (lastError !== undefined) updateData.lastError = lastError;
    if (lastErrorAt !== undefined) updateData.lastErrorAt = lastErrorAt;

    if (
      shouldMergeProviderSpecificData(
        existing.providerSpecificData,
        providerSpecificData,
        proxyConfig.hasAnyProxyField,
        proxyPoolResult.hasProxyPoolField
      )
    ) {
      updateData.providerSpecificData = {
        ...(existing.providerSpecificData || {}),
        ...(providerSpecificData || {}),
      };

      if (proxyConfig.hasAnyProxyField) {
        updateData.providerSpecificData.connectionProxyEnabled = proxyConfig.connectionProxyEnabled;
        updateData.providerSpecificData.connectionProxyUrl = proxyConfig.connectionProxyUrl;
        updateData.providerSpecificData.connectionNoProxy = proxyConfig.connectionNoProxy;
      }

      if (proxyPoolResult.hasProxyPoolField) {
        if (proxyPoolResult.proxyPoolId === null) {
          delete updateData.providerSpecificData.proxyPoolId;
        } else {
          updateData.providerSpecificData.proxyPoolId = proxyPoolResult.proxyPoolId;
        }
      }
    }

    const updated = await updateProviderConnection(id, updateData);

    // Hide sensitive fields
    const result = { ...updated };
    delete result.apiKey;
    delete result.accessToken;
    delete result.refreshToken;
    delete result.idToken;

    return res.json({ connection: result });
  } catch (error) {
    console.log("Error updating connection:", error);
    return res.status(500).json({ error: "Failed to update connection" });
  }
}

// DELETE /api/providers/[id] - Delete connection
export async function DELETE_handler(req, res, { params }) {
  try {
    const { id } = await params;
    const url = new URL('http://localhost' + req.originalUrl);
    const deleteFrom9router = url.searchParams.get("delete_from_9router") !== "false";

    const connection = await getProviderConnectionById(id);
    if (!connection) {
      return res.status(404).json({ error: "Connection not found" });
    }

    // 1. Delete matching CodeBuddy automation account if email and provider are present
    if (connection.email && connection.provider) {
      try {
        const accounts = await listCodeBuddyAccounts();
        const matchingAcc = accounts.find(
          a => (a.email || "").toLowerCase() === connection.email.toLowerCase() &&
               (a.provider || "") === connection.provider
        );
        if (matchingAcc) {
          await deleteCodeBuddyAccount(matchingAcc.id);
          if (matchingAcc.profileDir && fs.existsSync(matchingAcc.profileDir)) {
            try {
              fs.rmSync(matchingAcc.profileDir, { recursive: true, force: true });
            } catch (e) {
              console.error("Failed to delete profile dir:", matchingAcc.profileDir, e);
            }
          }
        }
      } catch (err) {
        console.error("Error looking up/deleting matching CodeBuddy account:", err);
      }
    }

    // 2. Delete connection from 9router (providerConnections) if requested
    if (deleteFrom9router) {
      const deleted = await deleteProviderConnection(id);
      if (!deleted) {
        return res.status(404).json({ error: "Connection not found" });
      }
    }

    return res.json({ message: "Connection processed successfully" });
  } catch (error) {
    console.log("Error deleting connection:", error);
    return res.status(500).json({ error: "Failed to delete connection" });
  }
}
