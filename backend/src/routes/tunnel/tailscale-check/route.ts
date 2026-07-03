import os from "os";
import { exec } from "child_process";
import { promisify } from "util";

import { isTailscaleInstalled, isTailscaleLoggedIn, TAILSCALE_SOCKET } from "../../../lib/tunnel/index.js";
import { getCachedPassword, loadEncryptedPassword } from "../../../mitm/manager.js";

const execAsync = promisify(exec);
const EXTENDED_PATH = `/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:${process.env.PATH || ""}`;
const PROBE_TIMEOUT_MS = 1500;

async function hasBrew() {
  try {
    await execAsync("which brew", { windowsHide: true, env: { ...process.env, PATH: EXTENDED_PATH }, timeout: PROBE_TIMEOUT_MS });
    return true;
  } catch { return false; }
}

async function isDaemonRunning() {
  try {
    await execAsync(`tailscale --socket ${TAILSCALE_SOCKET} status --json`, {
      windowsHide: true,
      env: { ...process.env, PATH: EXTENDED_PATH },
      timeout: PROBE_TIMEOUT_MS
    });
    return true;
  } catch {
    try {
      await execAsync("pgrep -x tailscaled", { windowsHide: true, timeout: PROBE_TIMEOUT_MS });
      return true;
    } catch { return false; }
  }
}

export async function GET(req, res) {
  try {
    const installed = isTailscaleInstalled();
    const platform = os.platform();
    // Run independent probes in parallel — none blocks the event loop
    const [brewAvailable, daemonRunning] = await Promise.all([
      platform === "darwin" ? hasBrew() : Promise.resolve(false),
      installed ? isDaemonRunning() : Promise.resolve(false),
    ]);
    const loggedIn = daemonRunning ? isTailscaleLoggedIn() : false;
    const hasCachedPassword = !!(getCachedPassword() || await loadEncryptedPassword());
    return res.json({ installed, loggedIn, platform, brewAvailable, daemonRunning, hasCachedPassword });
  } catch (error) {
    return res.status(500).json({ error: error.message });
  }
}
