
import {
  getProviderConnections,
  updateProviderConnection,
} from "../../../lib/localDb.js";

const MODEL_LOCK_PREFIX = "modelLock_";

function getActiveModelLocks(connection) {
  const now = Date.now();
  return Object.entries(connection)
    .filter(([key, value]) => key.startsWith(MODEL_LOCK_PREFIX) && value)
    .map(([key, value]) => ({
      key,
      model: key.slice(MODEL_LOCK_PREFIX.length) || "__all",
      until: value,
      active: new Date(value).getTime() > now,
    }))
    .filter((lock) => lock.active);
}

export async function GET(req, res) {
  try {
    const connections = await getProviderConnections();
    const models = [];

    for (const connection of connections) {
      const locks = getActiveModelLocks(connection);
      for (const lock of locks) {
        models.push({
          provider: connection.provider,
          model: lock.model,
          status: "cooldown",
          until: lock.until,
          connectionId: connection.id,
          connectionName: connection.name || connection.email || connection.id,
          lastError: connection.lastError || null,
        });
      }

      if (locks.length === 0 && connection.testStatus === "unavailable") {
        models.push({
          provider: connection.provider,
          model: "__all",
          status: "unavailable",
          connectionId: connection.id,
          connectionName: connection.name || connection.email || connection.id,
          lastError: connection.lastError || null,
        });
      }
    }

    return res.json({
      models,
      unavailableCount: models.length,
    });
  } catch (error) {
    console.error("[API] Failed to get model availability:", error);
    return res.json(
      { error: "Failed to fetch model availability" },
      { status: 500 },
    );
  }
}

export async function POST_handler(req, res) {
  try {
    const { action, provider, model } = req.body;

    if (action !== "clearCooldown" || !provider || !model) {
      return res.status(400).json({ error: "Invalid request" });
    }

    const connections = await getProviderConnections({ provider });
    const lockKey = `${MODEL_LOCK_PREFIX}${model}`;

    await Promise.all(
      connections
        .filter((connection) => connection[lockKey])
        .map((connection) =>
          updateProviderConnection(connection.id, {
            [lockKey]: null,
            ...(connection.testStatus === "unavailable"
              ? {
                  testStatus: "active",
                  lastError: null,
                  lastErrorAt: null,
                  backoffLevel: 0,
                }
              : {}),
          }),
        ),
    );

    return res.json({ ok: true });
  } catch (error) {
    console.error("[API] Failed to clear model cooldown:", error);
    return res.json(
      { error: "Failed to clear cooldown" },
      { status: 500 },
    );
  }
}
