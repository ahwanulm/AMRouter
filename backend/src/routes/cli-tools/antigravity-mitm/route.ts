
import {
  getMitmStatus,
  startServer,
  stopServer,
  enableToolDNS,
  disableToolDNS,
  trustCert,
  getCachedPassword,
  setCachedPassword,
  loadEncryptedPassword,
  isSudoPasswordRequired,
  initDbHooks,
} from "../../../mitm/manager.js";
import { getSettings, updateSettings } from "../../../lib/localDb.js";

initDbHooks(getSettings, updateSettings);

const DEFAULT_MITM_ROUTER_BASE = "http://localhost:3001";

function normalizeMitmRouterBaseUrlInput(input) {
  if (input == null || String(input).trim() === "") {
    return DEFAULT_MITM_ROUTER_BASE;
  }
  const t = String(input).trim().replace(/\/+$/, "");
  let u;
  try {
    u = new URL(t);
  } catch {
    throw new Error("Invalid MITM router URL");
  }
  if (u.protocol !== "http:" && u.protocol !== "https:") {
    throw new Error("MITM router URL must use http or https");
  }
  return t;
}

const isWin = process.platform === "win32";

function getPassword(provided) {
  return provided || getCachedPassword() || null;
}

function requiresSudoPassword(pwd) {
  return !isWin && !pwd && isSudoPasswordRequired();
}

function checkIsAdmin() {
  if (isWin) {
    try {
      require("child_process").execSync("net session >nul 2>&1", { windowsHide: true });
      return true;
    } catch {
      return false;
    }
  }
  return typeof process.getuid === "function" && process.getuid() === 0;
}

function checkPrivilege(pwd) {
  if (checkIsAdmin()) return true;
  if (isWin) return false;
  if (!isSudoPasswordRequired()) return true;
  return !!pwd;
}

// GET - Full MITM status (server + per-tool DNS)
export async function GET(req, res) {
  try {
    const status = await getMitmStatus();
    const settings = await getSettings();
    const hasCachedPassword = !!getCachedPassword() || !!(await loadEncryptedPassword());
    
    // Check if agy binary is installed in system
    let isAgyInstalled = false;
    try {
      const cmd = process.platform === "win32" ? "where agy" : "which agy";
      await execAsync(cmd, { windowsHide: true });
      isAgyInstalled = true;
    } catch {
      // Fallback: check standard paths or assume installed if DNS was ever enabled
      isAgyInstalled = status.dnsStatus?.antigravity || false;
    }

    return res.json({
      installed: isAgyInstalled,
      running: status.running,
      pid: status.pid || null,
      certExists: status.certExists || false,
      certTrusted: status.certTrusted || false,
      dnsStatus: status.dnsStatus || {},
      has9Router: status.dnsStatus?.antigravity || false, // configured if DNS is redirected
      hasCachedPassword,
      isWin,
      needsSudoPassword: !isWin && !hasCachedPassword && isSudoPasswordRequired(),
      isAdmin: checkIsAdmin(),
      mitmRouterBaseUrl:
        (settings.mitmRouterBaseUrl && String(settings.mitmRouterBaseUrl).trim()) ||
        DEFAULT_MITM_ROUTER_BASE,
    });
  } catch (error) {
    console.log("Error getting MITM status:", error.message);
    return res.status(500).json({ error: "Failed to get MITM status" });
  }
}

// POST - Start MITM server (cert + server, no DNS)
export async function POST_handler(req, res) {
  try {
    const { apiKey, sudoPassword, mitmRouterBaseUrl, forceKillPort443 } = req.body;
    const pwd = getPassword(sudoPassword) || await loadEncryptedPassword() || "";

    if (!apiKey || requiresSudoPassword(pwd)) {
      return res.json(
        { error: !apiKey ? "Missing apiKey" : "Missing sudoPassword" },
        { status: 400 }
      );
    }

    if (!checkPrivilege(pwd)) {
      return res.json(
        { error: isWin ? "Administrator required — restart 9Router as Administrator" : "Root or sudo password required to start MITM" },
        { status: 403 }
      );
    }

    if (mitmRouterBaseUrl !== undefined && mitmRouterBaseUrl !== null) {
      try {
        const normalized = normalizeMitmRouterBaseUrlInput(mitmRouterBaseUrl);
        await updateSettings({ mitmRouterBaseUrl: normalized });
      } catch (e) {
        return res.json(
          { error: e.message || "Invalid MITM router URL" },
          { status: 400 },
        );
      }
    }

    const result = await startServer(apiKey, pwd, !!forceKillPort443);
    if (!isWin) setCachedPassword(pwd);

    return res.json({ success: true, running: result.running, pid: result.pid });
  } catch (error) {
    console.log("Error starting MITM server:", error.message);
    if (error.code === "PORT_443_BUSY") {
      return res.json(
        { error: error.message, code: "PORT_443_BUSY", portOwner: error.portOwner },
        { status: 409 }
      );
    }
    return res.status(500).json({ error: error.message || "Failed to start MITM server" });
  }
}

// DELETE - Stop MITM server (removes all DNS first, then kills server)
export async function DELETE_handler(req, res) {
  try {
    const body = req.body || {};
    const { sudoPassword } = body;
    const pwd = getPassword(sudoPassword) || await loadEncryptedPassword() || "";

    if (requiresSudoPassword(pwd)) {
      return res.status(400).json({ error: "Missing sudoPassword" });
    }

    await stopServer(pwd);
    if (!isWin && sudoPassword) setCachedPassword(sudoPassword);

    return res.json({ success: true, running: false });
  } catch (error) {
    console.log("Error stopping MITM server:", error.message);
    return res.status(500).json({ error: error.message || "Failed to stop MITM server" });
  }
}

// PATCH - Toggle DNS for a specific tool (enable/disable)
export async function PATCH_handler(req, res) {
  try {
    const { tool, action, sudoPassword } = req.body;
    const pwd = getPassword(sudoPassword) || await loadEncryptedPassword() || "";

    if (!tool || !action) {
      return res.status(400).json({ error: "tool and action required" });
    }
    if (requiresSudoPassword(pwd)) {
      return res.status(400).json({ error: "Missing sudoPassword" });
    }
    if (!checkPrivilege(pwd)) {
      return res.json(
        { error: isWin ? "Administrator required — restart 9Router as Administrator" : "Root or sudo password required to modify DNS" },
        { status: 403 }
      );
    }

    if (action === "enable") {
      await enableToolDNS(tool, pwd);
    } else if (action === "disable") {
      await disableToolDNS(tool, pwd);
    } else if (action === "trust-cert") {
      await trustCert(pwd);
      if (!isWin && sudoPassword) setCachedPassword(sudoPassword);
      const status = await getMitmStatus();
      return res.json({ success: true, certTrusted: status.certTrusted });
    } else {
      return res.status(400).json({ error: "action must be enable, disable, or trust-cert" });
    }

    if (!isWin && sudoPassword) setCachedPassword(sudoPassword);

    const status = await getMitmStatus();
    return res.json({ success: true, dnsStatus: status.dnsStatus });
  } catch (error) {
    console.log("Error toggling DNS:", error.message);
    return res.status(500).json({ error: error.message || "Failed to toggle DNS" });
  }
}
