
import {
  deleteProxyPool,
  getProviderConnections,
  getProxyPoolById,
  updateProxyPool,
} from "../../../models/index.js";

function normalizeProxyPoolUpdate(body = {}) {
  const updates = {};

  if (Object.prototype.hasOwnProperty.call(body, "name")) {
    const name = typeof body?.name === "string" ? body.name.trim() : "";
    if (!name) {
      return { error: "Name is required" };
    }
    updates.name = name;
  }

  if (Object.prototype.hasOwnProperty.call(body, "proxyUrl")) {
    const proxyUrl = typeof body?.proxyUrl === "string" ? body.proxyUrl.trim() : "";
    if (!proxyUrl) {
      return { error: "Proxy URL is required" };
    }
    updates.proxyUrl = proxyUrl;
  }

  if (Object.prototype.hasOwnProperty.call(body, "noProxy")) {
    updates.noProxy = typeof body?.noProxy === "string" ? body.noProxy.trim() : "";
  }

  if (Object.prototype.hasOwnProperty.call(body, "isActive")) {
    updates.isActive = body?.isActive === true;
  }

  if (Object.prototype.hasOwnProperty.call(body, "strictProxy")) {
    updates.strictProxy = body?.strictProxy === true;
  }

  if (Object.prototype.hasOwnProperty.call(body, "type")) {
    const validTypes = ["http", "vercel", "cloudflare"];
    updates.type = validTypes.includes(body?.type) ? body.type : "http";
  }

  return { updates };
}

function countBoundConnections(connections = [], proxyPoolId) {
  return connections.filter((connection) => connection?.providerSpecificData?.proxyPoolId === proxyPoolId).length;
}

// GET /api/proxy-pools/[id] - Get proxy pool
export async function GET_handler(req, res, { params }) {
  try {
    const { id } = await params;
    const proxyPool = await getProxyPoolById(id);

    if (!proxyPool) {
      return res.status(404).json({ error: "Proxy pool not found" });
    }

    return res.json({ proxyPool });
  } catch (error) {
    console.log("Error fetching proxy pool:", error);
    return res.status(500).json({ error: "Failed to fetch proxy pool" });
  }
}

// PUT /api/proxy-pools/[id] - Update proxy pool
export async function PUT_handler(req, res, { params }) {
  try {
    const { id } = await params;
    const existing = await getProxyPoolById(id);

    if (!existing) {
      return res.status(404).json({ error: "Proxy pool not found" });
    }

    const body = req.body;
    const normalized = normalizeProxyPoolUpdate(body);

    if (normalized.error) {
      return res.status(400).json({ error: normalized.error });
    }

    const updated = await updateProxyPool(id, normalized.updates);
    return res.json({ proxyPool: updated });
  } catch (error) {
    console.log("Error updating proxy pool:", error);
    return res.status(500).json({ error: "Failed to update proxy pool" });
  }
}

// DELETE /api/proxy-pools/[id] - Delete proxy pool
export async function DELETE_handler(req, res, { params }) {
  try {
    const { id } = await params;
    const existing = await getProxyPoolById(id);

    if (!existing) {
      return res.status(404).json({ error: "Proxy pool not found" });
    }

    const connections = await getProviderConnections();
    const boundConnectionCount = countBoundConnections(connections, id);

    if (boundConnectionCount > 0) {
      return res.json(
        {
          error: "Proxy pool is currently in use",
          boundConnectionCount,
        },
        { status: 409 }
      );
    }

    await deleteProxyPool(id);
    return res.json({ success: true });
  } catch (error) {
    console.log("Error deleting proxy pool:", error);
    return res.status(500).json({ error: "Failed to delete proxy pool" });
  }
}
