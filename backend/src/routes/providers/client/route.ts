
import { getProviderConnections } from "../../../lib/localDb.js";
import { backfillCodexEmails } from "../../../lib/oauth/providers.js";
import { USAGE_APIKEY_PROVIDERS, USAGE_SUPPORTED_PROVIDERS } from "../../../shared/constants/providers.js";
import { getAdapter } from "../../../lib/db/driver.js";

const SAFE_FIELDS = [
  "id", "provider", "authType", "name", "email", "displayName",
  "priority", "globalPriority", "isActive", "defaultModel",
  "testStatus", "lastError", "lastErrorAt", "errorCode",
  "expiresAt", "lastUsedAt", "consecutiveUseCount",
  "createdAt", "updatedAt",
];

const SAFE_PSD_FIELDS = [
  "baseUrl", "azureEndpoint", "deployment", "apiVersion", "accountId",
  "region", "projectId", "resourceUrl", "proxyPoolId",
  "connectionProxyEnabled", "connectionProxyUrl", "connectionNoProxy",
  "githubLogin", "githubName", "githubEmail", "githubUserId",
  "username", "firstName", "lastName", "authMethod", "authKind",
];

const DEFAULT_PAGE_SIZE = 20;
const MAX_PAGE_SIZE = 500;

function maskName(name) {
  if (typeof name !== "string" || name.length <= 16) return name;
  if (/[a-zA-Z0-9_-]{32,}/.test(name)) return `${name.slice(0, 8)}***`;
  return name;
}

function sanitize(c) {
  const safe = {};
  for (const f of SAFE_FIELDS) if (c[f] !== undefined) safe[f] = c[f];
  if (safe.name) safe.name = maskName(safe.name);
  if (c.providerSpecificData) {
    const psd = {};
    for (const f of SAFE_PSD_FIELDS) {
      if (c.providerSpecificData[f] !== undefined) psd[f] = c.providerSpecificData[f];
    }
    safe.providerSpecificData = psd;
  }
  return safe;
}

function isUsageEligible(connection) {
  return USAGE_SUPPORTED_PROVIDERS.includes(connection.provider) && (
    connection.authType === "oauth" ||
    connection.authType === "cookie" ||
    USAGE_APIKEY_PROVIDERS.includes(connection.provider)
  );
}

function parsePositiveInt(value, fallback) {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function sortConnections(connections, sort, usageMap = {}) {
  const list = [...connections];

  if (sort === "usage-asc" || sort === "usage-desc") {
    return list.sort((a, b) => {
      const usedA = usageMap[a.id] ?? 0;
      const usedB = usageMap[b.id] ?? 0;
      // Least credits remaining = most usage = highest usedA
      const diff = sort === "usage-asc" ? usedB - usedA : usedA - usedB;
      if (diff !== 0) return diff;
      return (a.email || a.name || "").localeCompare(b.email || b.name || "");
    });
  }

  if (sort === "balance-asc" || sort === "balance-desc") {
    return list.sort((a, b) => {
      const balanceA = a.last_balance !== undefined && a.last_balance !== null ? Number(a.last_balance) : 0;
      const balanceB = b.last_balance !== undefined && b.last_balance !== null ? Number(b.last_balance) : 0;
      const diff = sort === "balance-asc" ? balanceA - balanceB : balanceB - balanceA;
      if (diff !== 0) return diff;
      return (a.email || a.name || "").localeCompare(b.email || b.name || "");
    });
  }

  if (sort === "provider") {
    return list.sort((a, b) => {
      const orderA = USAGE_SUPPORTED_PROVIDERS.indexOf(a.provider);
      const orderB = USAGE_SUPPORTED_PROVIDERS.indexOf(b.provider);
      if (orderA !== orderB) return orderA - orderB;
      return a.provider.localeCompare(b.provider);
    });
  }

  return list.sort((a, b) => {
    const priorityA = a.priority ?? Number.MAX_SAFE_INTEGER;
    const priorityB = b.priority ?? Number.MAX_SAFE_INTEGER;
    if (priorityA !== priorityB) return priorityA - priorityB;
    return (a.provider || "").localeCompare(b.provider || "");
  });
}

async function getTodayUsageMap(connectionIds) {
  try {
    const db = await getAdapter();
    const startOfDay = new Date();
    startOfDay.setHours(0, 0, 0, 0);
    const cutoff = startOfDay.toISOString();

    const rows = db.all(
      `SELECT connectionId, COUNT(*) as count
       FROM usageHistory
       WHERE timestamp >= ? AND connectionId IN (${connectionIds.map(() => "?").join(",")})
       GROUP BY connectionId`,
      [cutoff, ...connectionIds]
    );

    const map = {};
    for (const row of rows) map[row.connectionId] = row.count;
    return map;
  } catch {
    return {};
  }
}

export async function GET_handler(req, res) {
  try {
    await backfillCodexEmails();

    const { searchParams } = new URL('http://localhost' + req.originalUrl);
    const provider = searchParams.get("provider") || "all";
    const accountStatus = searchParams.get("accountStatus") || "all";
    const sort = searchParams.get("sort") || "priority";
    const page = parsePositiveInt(searchParams.get("page"), 1);
    const pageSize = Math.min(parsePositiveInt(searchParams.get("pageSize"), DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE);

    const allConnections = await getProviderConnections();
    const eligibleConnections = allConnections.filter(isUsageEligible);
    const providerOptions = Array.from(new Set(eligibleConnections.map((conn) => conn.provider))).sort();

    const providerFilteredConnections = eligibleConnections.filter((conn) => (
      provider === "all" || conn.provider === provider
    ));

    const accountFilteredConnections = providerFilteredConnections.filter((conn) => {
      if (accountStatus === "active") return conn.isActive ?? true;
      if (accountStatus === "inactive") return !(conn.isActive ?? true);
      return true;
    });

    let usageMap = {};
    if (sort === "usage-asc" || sort === "usage-desc") {
      const ids = accountFilteredConnections.map((c) => c.id);
      if (ids.length > 0) usageMap = await getTodayUsageMap(ids);
    }

    const sortedConnections = sortConnections(accountFilteredConnections, sort, usageMap);
    const total = sortedConnections.length;
    const totalPages = Math.max(1, Math.ceil(total / pageSize));
    const currentPage = Math.min(page, totalPages);
    const offset = (currentPage - 1) * pageSize;
    const pageConnections = sortedConnections.slice(offset, offset + pageSize).map(sanitize);

    return res.json({
      connections: pageConnections,
      providerOptions,
      pagination: {
        page: currentPage,
        pageSize,
        total,
        totalPages,
      },
      totals: {
        eligibleConnections: eligibleConnections.length,
        providerFilteredConnections: providerFilteredConnections.length,
      },
    });
  } catch (error) {
    console.log("Error fetching providers for client:", error);
    return res.status(500).json({ error: "Failed to fetch providers" });
  }
}
