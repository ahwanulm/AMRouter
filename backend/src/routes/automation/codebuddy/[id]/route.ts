
import { spawn } from "child_process";
import path from "path";
import fs from "fs";
import { v4 as uuidv4 } from "uuid";

// Parse any proxy string format → { server, username, password }
function parseProxyString(raw) {
  if (!raw) return null;
  raw = raw.trim();
  const badUrl = raw.match(/^(https?|socks[45]?):\/\/([^:]+):(\d+):([^:]+):(.+)$/);
  if (badUrl) {
    const [, , host, port, user, pass] = badUrl;
    return { server: `http://${host}:${port}`, username: user, password: pass };
  }
  const goodUrl = raw.match(/^(socks[45]?|https?|http):\/\/(?:([^:@]+):([^@]+)@)?([^:]+):(\d+)$/);
  if (goodUrl) {
    const [, proto, user, pass, host, port] = goodUrl;
    const r = { server: `${proto}://${host}:${port}` };
    if (user) r.username = user;
    if (pass) r.password = pass;
    return r;
  }
  const parts = raw.split(":");
  if (parts.length === 4 && /^\d+$/.test(parts[1]))
    return { server: `http://${parts[0]}:${parts[1]}`, username: parts[2], password: parts[3] };
  if (parts.length === 2 && /^\d+$/.test(parts[1]))
    return { server: `http://${parts[0]}:${parts[1]}` };
  return null;
}
import { getSettings } from "../../../../lib/localDb.js";
import { 
  getCodeBuddyAccount, deleteCodeBuddyAccount, markCodeBuddyRunning,
  markCodeBuddySuccess, markCodeBuddyError, markCanvaEnrolled, createCodeBuddyJob, updateCodeBuddyJobResult,
  createProviderConnection, getProviderConnections, updateCodeBuddyJobStatus,
  deleteProviderConnectionByEmailAndProvider
} from "../../../../lib/db/index.js";

export const dynamic = "force-dynamic";

export async function POST_handler(req, res, { params }) {
  try {
    const resolvedParams = await params;
    const accountId = parseInt(resolvedParams.id);
    const body = req.body;
    const { action, deleteFrom9router } = body;

    const account = await getCodeBuddyAccount(accountId);
    if (!account) {
      return res.status(404).json({ error: "Account not found" });
    }

    // ── Action: Run single account automation ────────────────────────
    if (action === "run") {
      if (global._codebuddyState?.activeJobId) {
        return res.status(400).json({ error: "Ada job lain yang sedang berjalan." });
      }

      const jobId = uuidv4();
      await createCodeBuddyJob(jobId, "signup", 1);
      
      // Background worker run single
      runSingleJob(jobId, accountId).catch(console.error);

      return res.json({ job_id: jobId, count: 1 });
    }

    // ── Action: Inject API key to 9router ───────────────────────────
    if (action === "add-to-9router") {
      if (account.apiKeyStatus !== "ready" || !account.apiKey) {
        return res.status(400).json({ error: "Akun belum ready (restricted atau gagal)." });
      }

      try {
        const provider = account.provider || "codebuddy";
        const connData = {
          provider: provider,
          authType: "apikey",
          name: account.email,
          apiKey: account.apiKey,
          email: account.email,
          priority: 1,
          isActive: true,
          testStatus: "unknown",
        };

        // Leonardo and Weavy use cookie-based auth — the apiKey in codebuddyAccounts
        // is actually the browser cookie string from the signup automation
        if (provider === "leonardo" || provider === "weavy") {
          connData.authType = "cookie";
          connData.cookie = account.apiKey;
          delete connData.apiKey;
        } else if (provider === "kimi-coding") {
          connData.authType = "oauth";
          connData.accessToken = account.apiKey;
        } else if (provider === "kiro" || provider === "qoder") {
          connData.accessToken = account.apiKey;
          if (provider === "qoder") {
            connData.providerSpecificData = {
              userId: account.email,
            };
          }
        }

        await createProviderConnection(connData);
        return res.json({ ok: true, email: account.email, message: `✓ ${account.email} berhasil ditambahkan ke provider ${provider} di 9router.` });
      } catch (e) {
        return res.status(500).json({ error: `Gagal menambahkan ke 9router: ${e.message}` });
      }
    }

    // ── Action: Delete account ───────────────────────────────────────
    if (action === "delete") {
      if (deleteFrom9router && account.email && account.provider) {
        await deleteProviderConnectionByEmailAndProvider(account.email, account.provider);
      }
      await deleteCodeBuddyAccount(accountId);
      
      // Delete profile dir
      if (account.profileDir && fs.existsSync(account.profileDir)) {
        try {
          fs.rmSync(account.profileDir, { recursive: true, force: true });
        } catch (e) {
          console.error("Failed to delete profile dir:", account.profileDir, e);
        }
      }
      return res.json({ ok: true });
    }

    return res.status(400).json({ error: "Unknown action" });
  } catch (error) {
    console.error("Error in POST /api/automation/codebuddy/[id]:", error);
    return res.status(500).json({ error: error.message });
  }
}

async function runSingleJob(jobId, accountId) {
  global._codebuddyState.activeJobId = jobId;
  global._codebuddyState.stopFlag = false;

  try {
    await updateCodeBuddyJobStatus(jobId, "running");
    const settings = await getSettings();

    await markCodeBuddyRunning(accountId);
    await updateCodeBuddyJobResult(jobId, 0, {
      status: "running",
      step: "Memulai otomatisasi browser..."
    });

    await executeCodeBuddySignupSingle(accountId, jobId, settings);
  } catch (e) {
    console.error("Error running single CodeBuddy job:", e);
  } finally {
    global._codebuddyState.activeJobId = null;
    const finalStatus = global._codebuddyState.stopFlag ? "stopped" : "completed";
    await updateCodeBuddyJobStatus(jobId, finalStatus);
  }
}

function executeCodeBuddySignupSingle(accountId, jobId, settings) {
  return new Promise(async (resolve, reject) => {
    try {
      const account = await getCodeBuddyAccount(accountId);
      if (!account) return reject(new Error("Account not found"));

      const isLeonardo = account.provider === "leonardo";
      const isWeavy = account.provider === "weavy";
      const isKimi = account.provider === "kimi-coding" || account.provider === "kimi";
      if (isLeonardo && !settings.leonardo_invite_link) {
        return reject(new Error("Leonardo invite link belum di-set di Settings."));
      }

      const venvPython = path.resolve(process.cwd(), ".venv/bin/python");
      const scriptPath = isLeonardo
        ? path.resolve(process.cwd(), "src/automation/leonardo_signup.py")
        : isWeavy
        ? path.resolve(process.cwd(), "src/automation/weavy_signup.py")
        : isKimi
        ? path.resolve(process.cwd(), "src/automation/kimi_signup.py")
        : path.resolve(process.cwd(), "src/automation/codebuddy_signup.py");
      const profilesDir = isLeonardo
        ? path.resolve(process.cwd(), "profiles/leonardo")
        : isWeavy
        ? path.resolve(process.cwd(), "profiles/weavy")
        : isKimi
        ? path.resolve(process.cwd(), "profiles/kimi")
        : path.resolve(process.cwd(), "profiles/codebuddy");

      const args = [
        scriptPath,
        `--email=${account.email}`,
        `--password=${account.password}`,
        `--profiles-dir=${profilesDir}`,
      ];

      if (isLeonardo) {
        args.push(`--invite-link=${settings.leonardo_invite_link}`);
        args.push(`--signup-method=${account.signupMethod || "google"}`);
        if (account.canvaEnrolled === 1) {
          args.push("--skip-canva");
        }
        if (settings.codebuddy_leave_canva_team === "1") {
          args.push("--leave-canva-team");
        }
      } else if (isWeavy) {
        if (account.signupMethod === "google" || (account.email && (account.email.endsWith("@gmosel.com") || account.email.endsWith("@gmail.com")))) {
          args.push("--gsuite");
        }
        args.push("--clean");
      }

      if (settings.codebuddy_browser_headless !== "0") {
        args.push("--headless");
      }

      if (settings.codebuddy_proxy_enabled === "1" && settings.codebuddy_proxy_pool) {
        try {
          const pool = JSON.parse(settings.codebuddy_proxy_pool);
          if (Array.isArray(pool) && pool.length > 0) {
            const chosen = pool[Math.floor(Math.random() * pool.length)];
            const parsed = parseProxyString(chosen);
            if (parsed) {
              args.push(`--proxy-server=${parsed.server}`);
              if (parsed.username) args.push(`--proxy-user=${parsed.username}`);
              if (parsed.password) args.push(`--proxy-pass=${parsed.password}`);
              console.log(`[proxy] Using: ${parsed.server} (auth: ${!!parsed.username})`);
            } else {
              console.error("[proxy] Could not parse:", chosen);
            }
          }
        } catch (e) {
          console.error("Failed to parse proxy pool:", e);
        }
      }

      try {
        fs.appendFileSync(
          "/home/ahwanulm/.9router-v2/logs/automation_spawn.log",
          `[${new Date().toISOString()}] [id]/route.js spawning python: ${venvPython} ${args.join(" ")}\n`
        );
      } catch (err) {
        console.error("Failed to write to automation_spawn.log:", err);
      }

      const child = spawn(venvPython, args, {
        env: { ...process.env, DISPLAY: process.env.DISPLAY || ":1" }
      });
      if (global._codebuddyState?.activeProcesses) {
        global._codebuddyState.activeProcesses.add(child);
      }
      let lastStep = "Browser diluncurkan...";
      let done = false;

      child.stdout.on("data", async (data) => {
        const lines = data.toString().split("\n");
        for (const line of lines) {
          if (!line.trim()) continue;
          try {
            const parsed = JSON.parse(line);
            if (parsed.step) {
              lastStep = parsed.step;
              await updateCodeBuddyJobResult(jobId, 0, {
                email: account.email,
                status: "running",
                step: lastStep
              });
            } else if (parsed.canva_enrolled) {
              await markCanvaEnrolled(account.id, 1);
              await updateCodeBuddyJobResult(jobId, 0, {
                email: account.email,
                status: "running",
                step: "Canva Enrolled. Menghubungkan ke Leonardo AI..."
              });
            } else if (parsed.status === "success") {
              done = true;
              const apiKeyToSave = (isLeonardo || isWeavy) ? parsed.cookie : parsed.api_key;
              await markCodeBuddySuccess(account.id, apiKeyToSave);
              await updateCodeBuddyJobResult(jobId, 0, {
                email: account.email,
                status: "done",
                api_key: apiKeyToSave,
                balance: parsed.balance,
                left_team: parsed.left_team,
                ok: true
              });

              if (settings.codebuddy_auto_9router === "1" || isLeonardo || isWeavy || isKimi) {
                try {
                  const provider = account.provider || "codebuddy";
                  const connData = {
                    provider: provider,
                    authType: (isLeonardo || isWeavy) ? "cookie" : isKimi ? "oauth" : "apikey",
                    name: account.email,
                    apiKey: apiKeyToSave,
                    email: account.email,
                    priority: 1,
                    isActive: true,
                    testStatus: (isLeonardo || isWeavy || isKimi) ? "active" : "unknown",
                  };

                  if (isLeonardo || isWeavy) {
                    connData.cookie = parsed.cookie;
                    connData.last_balance = parsed.balance !== undefined ? parsed.balance : 150;
                    if (isLeonardo || isWeavy) {
                      connData.accessToken = parsed.jwt;
                      connData.cached_jwt = parsed.jwt;
                      connData.jwt_expires_at = Math.floor(Date.now() / 1000) + 1800; // expires in 30 mins
                    }
                    if (isWeavy && (parsed.firebase_refresh_token || parsed.firebase_api_key)) {
                      connData.providerSpecificData = {
                        firebase_refresh_token: parsed.firebase_refresh_token || "",
                        firebase_api_key: parsed.firebase_api_key || "",
                      };
                    }
                  } else if (isKimi) {
                    connData.accessToken = parsed.api_key;
                    connData.refreshToken = parsed.refresh_token;
                    connData.expiresAt = parsed.expires_in
                      ? new Date(Date.now() + parsed.expires_in * 1000).toISOString()
                      : null;
                  } else if (provider === "kiro" || provider === "qoder") {
                    connData.accessToken = parsed.api_key;
                    if (provider === "qoder") {
                      connData.providerSpecificData = {
                        userId: account.email,
                      };
                    }
                  }

                  await createProviderConnection(connData);
                } catch (e) {
                  console.error("Auto add to 9router failed:", e);
                }
              }
            } else if (parsed.status === "error") {
              done = true;
              await markCodeBuddyError(account.id, parsed.message);
              await updateCodeBuddyJobResult(jobId, 0, {
                email: account.email,
                status: "failed",
                error: parsed.message,
                ok: false
              });
            }
          } catch (e) {
            // ignore
          }
        }
      });

      child.on("close", async (code) => {
        if (global._codebuddyState?.activeProcesses) {
          global._codebuddyState.activeProcesses.delete(child);
        }
        if (!done) {
          const errMsg = global._codebuddyState?.stopFlag 
            ? "Dihentikan oleh pengguna." 
            : `Proses terhenti dengan exit code ${code}.`;
          await markCodeBuddyError(account.id, errMsg);
          await updateCodeBuddyJobResult(jobId, 0, {
            email: account.email,
            status: "failed",
            error: errMsg,
            ok: false
          });
        }
        resolve();
      });

      child.on("error", async (err) => {
        if (global._codebuddyState?.activeProcesses) {
          global._codebuddyState.activeProcesses.delete(child);
        }
        if (!done) {
          const errMsg = global._codebuddyState?.stopFlag 
            ? "Dihentikan oleh pengguna." 
            : (err.message || String(err));
          await markCodeBuddyError(account.id, errMsg);
          await updateCodeBuddyJobResult(jobId, 0, {
            email: account.email,
            status: "failed",
            error: errMsg,
            ok: false
          });
        }
        resolve();
      });

    } catch (e) {
      reject(e);
    }
  });
}
