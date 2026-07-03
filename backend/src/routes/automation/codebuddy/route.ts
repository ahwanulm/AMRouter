

// Parse any proxy string format → { server, username, password }
function parseProxyString(raw) {
  if (!raw) return null;
  raw = raw.trim();

  // Malformed: http://host:port:user:pass (saved wrong to DB)
  const badUrl = raw.match(/^(https?|socks[45]?):\/\/([^:]+):(\d+):([^:]+):(.+)$/);
  if (badUrl) {
    const [, proto, host, port, user, pass] = badUrl;
    return { server: `http://${host}:${port}`, username: user, password: pass };
  }

  // Correct URL: http://user:pass@host:port
  const goodUrl = raw.match(/^(socks[45]?|https?|http):\/\/(?:([^:@]+):([^@]+)@)?([^:]+):(\d+)$/);
  if (goodUrl) {
    const [, proto, user, pass, host, port] = goodUrl;
    const r = { server: `${proto}://${host}:${port}` };
    if (user) r.username = user;
    if (pass) r.password = pass;
    return r;
  }

  // Plain: host:port:user:pass
  const parts = raw.split(":");
  if (parts.length === 4 && /^\d+$/.test(parts[1])) {
    return { server: `http://${parts[0]}:${parts[1]}`, username: parts[2], password: parts[3] };
  }

  // Plain: host:port
  if (parts.length === 2 && /^\d+$/.test(parts[1])) {
    return { server: `http://${parts[0]}:${parts[1]}` };
  }

  return null;
}
import { spawn } from "child_process";
import path from "path";
import fs from "fs";
import { v4 as uuidv4 } from "uuid";
import crypto from "crypto";
import { getSettings, updateSettings } from "../../../lib/localDb.js";
import { getAmmailClientFromSettings } from "../../../lib/automation/ammailClient.js";
import { 
  listCodeBuddyAccounts, getCodeBuddyAccount, insertCodeBuddyAccount,
  bulkDeleteCodeBuddyAccounts, deleteCodeBuddyAccount, markCodeBuddyRunning, markCodeBuddySuccess, markCodeBuddyError, markCanvaEnrolled,
  createCodeBuddyJob, getCodeBuddyJob, updateCodeBuddyJobStatus, updateCodeBuddyJobResult,
  createProviderConnection, getProviderConnections,
  deleteProviderConnectionByEmailAndProvider
} from "../../../lib/db/index.js";

// ── Human-like alias generator (ported from leoapi-main/app/store.py) ──
const _HUMAN_FIRST_NAMES = [
  "daniel","nisa","alif","rahma","rifki","putri","andi","budi",
  "citra","dewi","eka","fitri","galih","hana","indra","joko",
  "kiki","lina","maya","nanda","okta","putu","rendi","sari",
  "tania","umar","vina","wawan","yusuf","zahra","anggi","bagas",
  "dimas","elis","farah","gita","hadi","iman","jihan","khalid",
  "luna","mira","naufal","olivia","rizki","salsa","tegar","yoga",
  "ayu","rama","lia","yanto","wati","rian","intan","sigit",
  "alex","chris","emma","james","kate","leo","mike","nora",
  "oliver","paula","quinn","ryan","sara","tom","vera","will",
];

function generateHumanAlias() {
  const pick = (arr) => arr[crypto.randomInt(arr.length)];
  const digits = (n) => Array.from({ length: n }, () => crypto.randomInt(10)).join("");
  const first = pick(_HUMAN_FIRST_NAMES);
  const style = crypto.randomInt(4);
  const dLen = 2 + crypto.randomInt(3); // 2-4 digits
  if (style === 0) return `${first}${digits(dLen)}`;
  if (style === 1) return `${first}.${digits(dLen)}`;
  if (style === 2) return `${first}_${digits(dLen)}`;
  let second = pick(_HUMAN_FIRST_NAMES);
  while (second === first) second = pick(_HUMAN_FIRST_NAMES);
  return `${first}.${second}${digits(1 + crypto.randomInt(3))}`;
}

function generateStrongPassword(length = 16) {
  const lower = "abcdefghijklmnopqrstuvwxyz";
  const upper = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
  const digit = "0123456789";
  const symbol = "!@#$%^&*-_=+";
  const all = lower + upper + digit + symbol;
  const pick = (s) => s[crypto.randomInt(s.length)];
  const chars = [pick(lower), pick(upper), pick(digit), pick(symbol)];
  for (let i = 4; i < length; i++) chars.push(pick(all));
  // Fisher-Yates shuffle
  for (let i = chars.length - 1; i > 0; i--) {
    const j = crypto.randomInt(i + 1);
    [chars[i], chars[j]] = [chars[j], chars[i]];
  }
  return chars.join("");
}

// Global job runner state
if (!global._codebuddyState) {
  global._codebuddyState = {
    activeJobId: null,
    stopFlag: false,
    activeProcesses: new Set()
  };
} else if (!global._codebuddyState.activeProcesses) {
  global._codebuddyState.activeProcesses = new Set();
}

export const dynamic = "force-dynamic";

export async function GET(req, res) {
  try {
    const { getAdapter } = await import("../../../lib/db/driver.js");
    const db = await getAdapter();

    let activeJobId = global._codebuddyState.activeJobId || "";

    // Heal dangling running state for jobs & accounts when no job is active in memory
    if (!activeJobId) {
      try {
        db.run("UPDATE codebuddyAccounts SET apiKeyStatus = 'failed', lastError = 'Job terhenti atau di-stop.' WHERE apiKeyStatus = 'running'");
        const lastJob = db.get("SELECT * FROM codebuddyJobs ORDER BY createdAt DESC LIMIT 1");
        if (lastJob) {
          let results = [];
          try {
            results = JSON.parse(lastJob.resultsJson || "[]");
          } catch (e) {}
          let modified = false;
          for (const r of results) {
            if (r && r.status === "running") {
              r.status = "failed";
              r.error = "Dihentikan oleh pengguna atau server restart.";
              r.ok = false;
              modified = true;
            }
          }
          const newStatus = lastJob.status === "running" ? "stopped" : lastJob.status;
          if (lastJob.status === "running" || modified) {
            db.run(
              "UPDATE codebuddyJobs SET status = ?, resultsJson = ? WHERE id = ?",
              [newStatus, JSON.stringify(results), lastJob.id]
            );
          }
        }
      } catch (e) {
        console.error("Heal dangling states error:", e);
      }
    }

    const accounts = await listCodeBuddyAccounts();
    const settings = await getSettings();
    
    let activeJob = null;
    if (activeJobId) {
      const job = await getCodeBuddyJob(activeJobId);
      if (job) {
        activeJob = {
          id: job.id,
          status: job.status,
          count: job.count,
          completed: job.completed || 0,
          success: job.success || 0,
          failed: job.failed || 0,
          progress: job.progress || 0,
          results: job.results || [],
          createdAt: job.createdAt,
        };
      }
    } else {
      // Get the last job from DB to show completion stats
      try {
        const job = db.get("SELECT * FROM codebuddyJobs WHERE status != 'dismissed' ORDER BY createdAt DESC LIMIT 1");
        if (job) {
          let results = [];
          try {
            results = JSON.parse(job.resultsJson || "[]");
          } catch (e) {}
          activeJob = {
            id: job.id,
            status: job.status,
            count: job.count,
            completed: job.completed || 0,
            success: job.success || 0,
            failed: job.failed || 0,
            progress: job.progress || 0,
            results: results,
            createdAt: job.createdAt,
          };
        }
      } catch (e) {
        console.error("Failed to load last job from DB:", e);
      }
    }

    return res.json({
      accounts: accounts.map(a => ({
        id: a.id,
        email: a.email,
        api_key: a.apiKey,
        api_key_status: a.apiKeyStatus,
        last_error: a.lastError,
        provider: a.provider || "codebuddy",
      })),
      active_job_id: activeJobId,
      active_job: activeJob,
      settings: {
        auto_9router: settings.codebuddy_auto_9router || "0",
        browser_headless: settings.codebuddy_browser_headless !== "0",
        debug_mode: settings.codebuddy_debug_mode === "1",
        leave_canva_team: settings.codebuddy_leave_canva_team || "0",
        proxy_enabled: settings.codebuddy_proxy_enabled === "1",
        proxy_pool: settings.codebuddy_proxy_pool || "[]",
        leonardo_invite_link: settings.leonardo_invite_link || "",
        codebuddy_2captcha_api_key: settings.codebuddy_2captcha_api_key || "",
      }
    });
  } catch (error) {
    console.error("Error in GET /api/automation/codebuddy:", error);
    return res.status(500).json({ error: error.message });
  }
}

export async function POST_handler(req, res) {
  try {
    const body = req.body;
    const { action } = body;

    // ── Action: Settings ─────────────────────────────────────────────
    if (action === "settings") {
      const { auto_9router, browser_headless, debug_mode, leave_canva_team, proxy_enabled, proxy_pool, leonardo_invite_link, codebuddy_2captcha_api_key } = body;
      const updates = {};
      if (auto_9router !== undefined) updates.codebuddy_auto_9router = auto_9router ? "1" : "0";
      if (browser_headless !== undefined) updates.codebuddy_browser_headless = browser_headless ? "1" : "0";
      if (debug_mode !== undefined) updates.codebuddy_debug_mode = debug_mode ? "1" : "0";
      if (leave_canva_team !== undefined) updates.codebuddy_leave_canva_team = leave_canva_team ? "1" : "0";
      if (proxy_enabled !== undefined) updates.codebuddy_proxy_enabled = proxy_enabled ? "1" : "0";
      if (proxy_pool !== undefined) updates.codebuddy_proxy_pool = proxy_pool;
      if (leonardo_invite_link !== undefined) updates.leonardo_invite_link = leonardo_invite_link;
      if (codebuddy_2captcha_api_key !== undefined) updates.codebuddy_2captcha_api_key = codebuddy_2captcha_api_key;
      
      await updateSettings(updates);
      return res.json({ ok: true });
    }

    // ── Action: Auto Generate Email ──────────────────────────────────
    if (action === "auto-generate-email") {
      const { count, provider, run_now, concurrency, domain } = body;
      const targetProvider = provider || "leonardo";
      const numCount = parseInt(count) || 1;

      const client = await getAmmailClientFromSettings();
      if (!client.configured) {
        return res.status(400).json({ error: "Ammail belum dikonfigurasi di Settings." });
      }

      const createdAccounts = [];
      const errors = [];

      for (let i = 0; i < numCount; i++) {
        try {
          // Retry up to 3x with different human aliases (collision handling)
          let res = null;
          let lastErr = null;
          for (let attempt = 0; attempt < 3; attempt++) {
            const aliasTry = generateHumanAlias();
            try {
              res = await client.createInbox(aliasTry, domain || null);
              break;
            } catch (err) {
              lastErr = err;
              const msg = String(err.message || err).toLowerCase();
              // Alias collision — try a different one
              if (msg.includes("alias") && (msg.includes("exist") || msg.includes("taken") || msg.includes("conflict"))) {
                continue;
              }
              break; // Other errors (auth, network) — stop retrying
            }
          }
          if (!res || !res.inbox || !res.inbox.address) {
            throw lastErr || new Error("Gagal membuat inbox dari Ammail");
          }

          const email = res.inbox.address;
          const alias = res.inbox.alias;
          const password = generateStrongPassword(16);

          const profilesDir = path.resolve(process.cwd(), `profiles/${targetProvider}`);
          const safeEmail = email.replace("@", "_at_").replace(/[^a-z0-9._-]+/g, "_");
          const profileDir = path.join(profilesDir, safeEmail);

          const newId = await insertCodeBuddyAccount(email, password, profileDir, "email", alias, targetProvider);
          createdAccounts.push({ id: newId, email });
        } catch (err) {
          errors.push(err.message || String(err));
        }
      }

      const response = {
        ok: true,
        created: createdAccounts,
        errors
      };

      if (run_now && createdAccounts.length > 0) {
        const targetIds = createdAccounts.map(a => a.id);
        const jobId = uuidv4();
        const concurrencyLimit = parseInt(concurrency) || 3;
        await createCodeBuddyJob(jobId, "signup", targetIds.length);
        runCodeBuddySignupJob(jobId, targetIds, concurrencyLimit).catch(console.error);
        response.job_id = jobId;
      }

      return res.json(response);
    }

    // ── Action: Add manual Google accounts ───────────────────────────
    if (action === "add-google") {
      const { accounts_text, run_now, concurrency, provider } = body;
      const targetProvider = provider || "codebuddy";
      console.log(`[ADD-GOOGLE] Adding accounts for target provider: "${targetProvider}"`);
      const raw = (accounts_text || "").trim();
      if (!raw) {
        return res.status(400).json({ error: "Input kosong." });
      }

      const existingAccounts = await listCodeBuddyAccounts();
      const existingEmails = new Set(
        existingAccounts
          .filter(a => (a.provider || "codebuddy") === targetProvider)
          .map(a => a.email.toLowerCase())
      );
      console.log(`[ADD-GOOGLE] Found ${existingEmails.size} existing accounts for provider "${targetProvider}".`);

      const targetIds = [];
      const skipped = [];
      const parseErrors = [];

      const lines = raw.split("\n");
      for (let line of lines) {
        line = line.trim();
        if (!line || line.startsWith("#")) continue;

        const parts = line.replace(",", ":").split(":");
        if (parts.length < 2) {
          parseErrors.append ? parseErrors.append(line) : parseErrors.push(line);
          continue;
        }

        const email = parts[0].trim().toLowerCase();
        const password = parts.slice(1).join(":").trim();
        if (!email || !password) {
          parseErrors.push(line);
          continue;
        }

        const alreadyExists = existingEmails.has(email);
        if (alreadyExists) {
          console.log(`[ADD-GOOGLE] Skipping email "${email}" (already exists for provider "${targetProvider}")`);
          skipped.push(email);
          continue;
        }

        const profilesDir = targetProvider === "leonardo"
          ? path.resolve(process.cwd(), "profiles/leonardo")
          : targetProvider === "weavy"
          ? path.resolve(process.cwd(), "profiles/weavy")
          : (targetProvider === "kimi-coding" || targetProvider === "kimi")
          ? path.resolve(process.cwd(), "profiles/kimi")
          : targetProvider === "qoder"
          ? path.resolve(process.cwd(), "profiles/qoder")
          : path.resolve(process.cwd(), "profiles/codebuddy");
        const safeEmail = email.replace("@", "_at_").replace(/[^a-z0-9._-]+/g, "_");
        const profileDir = path.join(profilesDir, safeEmail);

        const newId = await insertCodeBuddyAccount(email, password, profileDir, "google", "", targetProvider);
        targetIds.push(newId);
        existingEmails.add(email);
      }

      const response = {
        created: targetIds,
        skipped,
        parse_errors: parseErrors,
      };

      if (run_now && targetIds.length > 0) {
        const jobId = uuidv4();
        const concurrencyLimit = parseInt(concurrency) || 3;
        await createCodeBuddyJob(jobId, "signup", targetIds.length);
        
        // Start background runner async
        runCodeBuddySignupJob(jobId, targetIds, concurrencyLimit).catch(console.error);
        response.job_id = jobId;
      }

      return res.json(response);
    }

    // ── Action: Run all pending/failed accounts ─────────────────────
    if (action === "run-all") {
      const concurrencyLimit = parseInt(body.concurrency) || 3;
      const { provider } = body;
      const accounts = await listCodeBuddyAccounts();
      const targetIds = accounts
        .filter(a => {
          const matchesStatus = a.apiKeyStatus === "pending" || a.apiKeyStatus === "failed";
          const matchesProvider = provider ? (a.provider || "codebuddy") === provider : true;
          return matchesStatus && matchesProvider;
        })
        .map(a => a.id);

      if (targetIds.length === 0) {
        return res.status(400).json({ error: "Tidak ada akun pending/failed." });
      }

      if (global._codebuddyState.activeJobId) {
        return res.status(400).json({ error: "Ada job lain yang sedang berjalan." });
      }

      const jobId = uuidv4();
      await createCodeBuddyJob(jobId, "signup", targetIds.length);
      runCodeBuddySignupJob(jobId, targetIds, concurrencyLimit).catch(console.error);

      return res.json({ job_id: jobId, count: targetIds.length });
    }

    // ── Action: Stop active job ──────────────────────────────────────
    if (action === "stop") {
      const busy = global._codebuddyState.activeJobId;
      if (!busy) {
        return res.json({ ok: true, message: "Tidak ada job aktif." });
      }

      global._codebuddyState.stopFlag = true;

      // Kill all active processes
      if (global._codebuddyState.activeProcesses) {
        for (const child of global._codebuddyState.activeProcesses) {
          try {
            child.kill("SIGTERM");
          } catch (err) {
            console.error("Failed to kill child process:", err);
          }
        }
        global._codebuddyState.activeProcesses.clear();
      }

      return res.json({
        ok: true,
        active_job_id: busy,
        message: "Stop signal terkirim. Akun yang sedang berjalan akan dihentikan.",
      });
    }

    // ── Action: Bulk delete accounts ────────────────────────────────
    if (action === "bulk-delete") {
      const { statuses, provider, deleteFrom9router } = body;
      const allowed = new Set(["pending", "failed", "ready"]);
      const validStatuses = (statuses || []).filter(s => allowed.has(s));
      if (validStatuses.length === 0) {
        return res.status(400).json({ error: "No valid statuses provided" });
      }

      const deleted = await bulkDeleteCodeBuddyAccounts(validStatuses, provider);
      
      // Delete their profiles folders
      for (const acc of deleted) {
        if (deleteFrom9router && acc.email && acc.provider) {
          await deleteProviderConnectionByEmailAndProvider(acc.email, acc.provider);
        }
        if (acc.profileDir && fs.existsSync(acc.profileDir)) {
          try {
            fs.rmSync(acc.profileDir, { recursive: true, force: true });
          } catch (e) {
            console.error("Failed to delete profile dir:", acc.profileDir, e);
          }
        }
      }

      return res.json({ deleted: deleted.length, statuses: validStatuses });
    }

    if (action === "bulk-delete-ids") {
      const { ids, provider, deleteFrom9router } = body;
      if (!Array.isArray(ids) || ids.length === 0) {
        return res.status(400).json({ error: "No IDs provided" });
      }
      let deletedCount = 0;
      for (const id of ids) {
        try {
          const acc = await getCodeBuddyAccount(id);
          if (acc) {
            if (deleteFrom9router && acc.email && acc.provider) {
              await deleteProviderConnectionByEmailAndProvider(acc.email, acc.provider);
            }
            await deleteCodeBuddyAccount(id);
            if (acc.profileDir && fs.existsSync(acc.profileDir)) {
              fs.rmSync(acc.profileDir, { recursive: true, force: true });
            }
            deletedCount++;
          }
        } catch (e) {
          console.error("Failed to delete account", id, e);
        }
      }
      return res.json({ deleted: deletedCount });
    }

    // ── Action: Bulk add ready accounts to 9router ─────────────────
    if (action === "bulk-add-to-9router") {
      const { provider } = body;
      const accounts = await listCodeBuddyAccounts();
      const ready = accounts.filter(a => {
        const matchesStatus = a.apiKeyStatus === "ready" && a.apiKey;
        const matchesProvider = provider ? (a.provider || "codebuddy") === provider : true;
        return matchesStatus && matchesProvider;
      });
      if (ready.length === 0) {
        return res.json({ ok: true, total: 0, success: 0, failed: 0, message: "Tidak ada akun ready." });
      }

      let success = 0;
      let failed = 0;
      const errors = [];

      for (const acc of ready) {
        try {
          const provider = acc.provider || "codebuddy";
          const connData = {
            provider: provider,
            authType: "apikey",
            name: acc.email,
            apiKey: acc.apiKey,
            email: acc.email,
            priority: 1,
            isActive: true,
            testStatus: "unknown",
          };

          // Leonardo and Weavy use cookie-based auth
          if (provider === "leonardo" || provider === "weavy") {
            connData.authType = "cookie";
            connData.cookie = acc.apiKey;
            delete connData.apiKey;
          } else if (provider === "kimi-coding") {
            connData.authType = "oauth";
            connData.accessToken = acc.apiKey;
          } else if (provider === "kiro" || provider === "qoder") {
            connData.accessToken = acc.apiKey;
            if (provider === "qoder") {
              connData.providerSpecificData = {
                userId: acc.email,
              };
            }
          }

          await createProviderConnection(connData);
          success++;
        } catch (e) {
          failed++;
          errors.push({ email: acc.email, error: e.message || String(e) });
        }
      }

      return res.json({
        ok: true,
        total: ready.length,
        success,
        failed,
        errors,
        message: `✓ ${success}/${ready.length} akun berhasil ditambahkan ke 9router.`
      });
    }

    if (action === "clear-logs") {
      try {
        const { getAdapter } = await import("../../../lib/db/driver.js");
        const db = await getAdapter();
        // Update all completed/failed/stopped jobs to dismissed
        db.run(
          "UPDATE codebuddyJobs SET status = 'dismissed' WHERE status IN ('completed', 'failed', 'stopped', 'error')"
        );
        // Also clear memory state if no job is actually running
        if (global._codebuddyState && !global._codebuddyState.activeProcesses?.size) {
          global._codebuddyState.activeJobId = null;
        }
      } catch (e) {
        console.error("clear-logs error:", e);
      }
      return res.json({ ok: true });
    }

    return res.status(400).json({ error: "Unknown action" });
  } catch (error) {
    console.error("Error in POST /api/automation/codebuddy:", error);
    return res.status(500).json({ error: error.message });
  }
}

// ── Background Runner Loop ───────────────────────────────────────────
async function runCodeBuddySignupJob(jobId, accountIds, concurrencyLimit) {
  global._codebuddyState.activeJobId = jobId;
  global._codebuddyState.stopFlag = false;

  try {
    await updateCodeBuddyJobStatus(jobId, "running");
    const settings = await getSettings();

    let index = 0;
    const total = accountIds.length;

    const worker = async () => {
      while (index < total) {
        if (global._codebuddyState.stopFlag) break;
        const currentIdx = index++;
        const accountId = accountIds[currentIdx];

        await markCodeBuddyRunning(accountId);
        await updateCodeBuddyJobResult(jobId, currentIdx, {
          status: "running",
          step: "Memulai otomatisasi browser..."
        });

        try {
          await executeCodeBuddySignup(accountId, jobId, currentIdx, settings);
        } catch (e) {
          await markCodeBuddyError(accountId, e.message || String(e));
          await updateCodeBuddyJobResult(jobId, currentIdx, {
            status: "failed",
            error: e.message || String(e),
            ok: false
          });
        }
      }
    };

    const workers = [];
    const limit = Math.min(concurrencyLimit, total);
    for (let i = 0; i < limit; i++) {
      workers.push(worker());
    }

    await Promise.all(workers);
  } catch (e) {
    console.error("Error running CodeBuddy job:", e);
  } finally {
    global._codebuddyState.activeJobId = null;
    const finalStatus = global._codebuddyState.stopFlag ? "stopped" : "completed";
    await updateCodeBuddyJobStatus(jobId, finalStatus);
  }
}

function executeCodeBuddySignup(accountId, jobId, idx, settings) {
  return new Promise(async (resolve, reject) => {
    try {
      const account = await getCodeBuddyAccount(accountId);
      if (!account) return reject(new Error("Account not found"));

      const isLeonardo = account.provider === "leonardo";
      const isWeavy = account.provider === "weavy";
      const isKimi = account.provider === "kimi-coding" || account.provider === "kimi";
      const isQoder = account.provider === "qoder";
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
        : isQoder
        ? path.resolve(process.cwd(), "src/automation/qoder_signup.py")
        : path.resolve(process.cwd(), "src/automation/codebuddy_signup.py");
      const profilesDir = isLeonardo
        ? path.resolve(process.cwd(), "profiles/leonardo")
        : isWeavy
        ? path.resolve(process.cwd(), "profiles/weavy")
        : isKimi
        ? path.resolve(process.cwd(), "profiles/kimi")
        : isQoder
        ? path.resolve(process.cwd(), "profiles/qoder")
        : path.resolve(process.cwd(), "profiles/codebuddy");

      const args = [
        scriptPath,
        `--email=${account.email}`,
        `--password=${account.password}`,
        `--profiles-dir=${profilesDir}`,
      ];

      if (isLeonardo) {
        args.push(`--invite-link=${settings.leonardo_invite_link || ""}`);
        args.push(`--signup-method=${account.signupMethod || "google"}`);
        if (account.canvaEnrolled === 1) {
          args.push("--skip-canva");
        } else {
          // Canva step: always non-headless to avoid bot detection.
          // Do NOT pass --canva-headless → Camoufox runs visible for Canva.
          // Add random pre-Canva delay (15-30s) to reduce rate-limit detection.
          args.push("--canva-delay=30");
        }
        if (settings.codebuddy_leave_canva_team === "1") {
          args.push("--leave-canva-team");
        }
      } else if (isWeavy) {
        if (account.signupMethod === "google" || account.email.endsWith("@gmosel.com") || account.email.endsWith("@gmail.com")) {
          args.push("--gsuite");
        }
        args.push("--clean");
      }

      if (settings.codebuddy_browser_headless !== "0") {
        args.push("--headless");
      }

      if (settings.codebuddy_proxy_enabled === "1" && settings.codebuddy_proxy_pool && !isQoder) {
        try {
          const pool = JSON.parse(settings.codebuddy_proxy_pool);
          if (Array.isArray(pool) && pool.length > 0) {
            // Round-robin anti-repeat: track last used index globally so the
            // same IP is not reused in back-to-back signups.
            if (!global._codebuddyState.proxyRoundRobinIdx) {
              global._codebuddyState.proxyRoundRobinIdx = 0;
            }
            const idx = global._codebuddyState.proxyRoundRobinIdx % pool.length;
            global._codebuddyState.proxyRoundRobinIdx = (idx + 1) % pool.length;
            const chosen = pool[idx];
            const parsed = parseProxyString(chosen);
            if (parsed) {
              args.push(`--proxy-server=${parsed.server}`);
              if (parsed.username) args.push(`--proxy-user=${parsed.username}`);
              if (parsed.password) args.push(`--proxy-pass=${parsed.password}`);
              console.log(`[proxy] Using (slot ${idx}/${pool.length}): ${parsed.server} (auth: ${!!parsed.username})`);
            } else {
              console.error("[proxy] Could not parse proxy string:", chosen);
            }
          }
        } catch (e) {
          console.error("Failed to parse proxy pool:", e);
        }
      }

      try {
        fs.appendFileSync(
          "/home/ahwanulm/.9router-v2/logs/automation_spawn.log",
          `[${new Date().toISOString()}] route.js spawning python: ${venvPython} ${args.join(" ")}\n`
        );
      } catch (err) {
        console.error("Failed to write to automation_spawn.log:", err);
      }

      const child = spawn(venvPython, args, {
        env: { ...process.env, DISPLAY: process.env.DISPLAY || ":1" }
      });
      if (global._codebuddyState.activeProcesses) {
        global._codebuddyState.activeProcesses.add(child);
      }
      let stderrAccumulator = "";
      child.stderr.on("data", (data) => {
        console.error(`[child stderr] ${data.toString()}`);
        stderrAccumulator += data.toString();
      });
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
              await updateCodeBuddyJobResult(jobId, idx, {
                email: account.email,
                status: "running",
                step: lastStep
              });
            } else if (parsed.canva_enrolled) {
              await markCanvaEnrolled(account.id, 1);
              await updateCodeBuddyJobResult(jobId, idx, {
                email: account.email,
                status: "running",
                step: "Canva Enrolled. Menghubungkan ke Leonardo AI..."
              });
            } else if (parsed.status === "success") {
              done = true;
              const apiKeyToSave = (isLeonardo || isWeavy) ? parsed.cookie : parsed.api_key;
              await markCodeBuddySuccess(account.id, apiKeyToSave);
              await updateCodeBuddyJobResult(jobId, idx, {
                email: account.email,
                status: "done",
                api_key: apiKeyToSave,
                balance: parsed.balance,
                left_team: parsed.left_team,
                ok: true
              });

              if (settings.codebuddy_auto_9router === "1" || isLeonardo || isWeavy || isKimi || isQoder) {
                try {
                  const provider = account.provider || "codebuddy";
                  const connData = {
                    provider: provider,
                    authType: (isLeonardo || isWeavy) ? "cookie" : (isKimi || isQoder) ? "oauth" : "apikey",
                    name: account.email,
                    apiKey: apiKeyToSave,
                    email: account.email,
                    priority: 1,
                    isActive: true,
                    testStatus: (isLeonardo || isWeavy || isKimi || isQoder) ? "active" : "unknown",
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
                  } else if (isQoder) {

                    connData.accessToken = parsed.api_key;
                    connData.refreshToken = parsed.refresh_token;
                    connData.expiresAt = parsed.expires_in
                      ? new Date(Date.now() + parsed.expires_in * 1000).toISOString()
                      : null;
                    connData.displayName = parsed.name || null;
                    connData.email = parsed.email || account.email;
                    connData.providerSpecificData = {
                      authMethod: "device",
                      userId: parsed.user_id || "",
                      machineId: parsed.machine_id || "",
                      organizationId: parsed.organization_id || "",
                    };
                  } else if (provider === "kiro") {
                    connData.accessToken = apiKeyToSave;
                  }

                  await createProviderConnection(connData);
                } catch (e) {
                  console.error("Auto add to 9router failed:", e);
                }
              }
            } else if (parsed.status === "error") {
              done = true;
              await markCodeBuddyError(account.id, parsed.message);
              await updateCodeBuddyJobResult(jobId, idx, {
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
        if (global._codebuddyState.activeProcesses) {
          global._codebuddyState.activeProcesses.delete(child);
        }
        if (!done) {
          let errMsg = global._codebuddyState.stopFlag 
            ? "Dihentikan oleh pengguna." 
            : `Proses terhenti dengan exit code ${code}.`;
          if (stderrAccumulator.trim()) {
            errMsg += ` | Stderr: ${stderrAccumulator.trim()}`;
          }
          await markCodeBuddyError(account.id, errMsg);
          await updateCodeBuddyJobResult(jobId, idx, {
            email: account.email,
            status: "failed",
            error: errMsg,
            ok: false
          });
        }
        resolve();
      });

      child.on("error", async (err) => {
        if (global._codebuddyState.activeProcesses) {
          global._codebuddyState.activeProcesses.delete(child);
        }
        if (!done) {
          const errMsg = global._codebuddyState.stopFlag 
            ? "Dihentikan oleh pengguna." 
            : (err.message || String(err));
          await markCodeBuddyError(account.id, errMsg);
          await updateCodeBuddyJobResult(jobId, idx, {
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
