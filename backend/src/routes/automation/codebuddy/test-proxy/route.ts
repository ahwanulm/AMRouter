
import { spawnSync } from "child_process";
import path from "path";

const SCRIPT = path.join(process.cwd(), "src/automation/test_proxy.py");

// Find venv python
function getVenvPython() {
  const venvPy = path.join(process.cwd(), ".venv/bin/python3");
  try {
    const r = spawnSync(venvPy, ["--version"], { timeout: 3000 });
    if (r.status === 0) return venvPy;
  } catch {}
  return "python3";
}

export async function POST_handler(req, res) {
  try {
    const { proxy } = req.body;
    if (!proxy || typeof proxy !== "string") {
      return res.status(400).json({ ok: false, error: "No proxy provided" });
    }

    const python = getVenvPython();
    const result = spawnSync(python, [
      SCRIPT,
      "--proxy", proxy.trim(),
      "--headless",
    ], {
      timeout: 35000,
      encoding: "utf-8",
      env: { ...process.env, DISPLAY: process.env.DISPLAY || ":1" },
    });

    const stdout = (result.stdout || "").trim();
    const stderr = (result.stderr || "").trim();

    if (!stdout) {
      return res.json({
        ok: false,
        error: stderr || `Script error (exit ${result.status})`,
      });
    }

    try {
      const data = JSON.parse(stdout);
      return res.json(data);
    } catch {
      return res.json({ ok: false, error: stdout.substring(0, 200) });
    }
  } catch (e) {
    return res.status(500).json({ ok: false, error: e.message });
  }
}
