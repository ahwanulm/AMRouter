
import { makeKv } from "../../../lib/db/helpers/kvStore.js";
import { getProviderModels } from "../../../../open-sse/config/providerModels.js";

const leoKv = makeKv("leonardo");
const CONFIG_KEY = "admin_config";

// Default capabilities per model type + known patterns
// Mirrors kliperspro's DEFAULT_CONFIG patterns
const DEFAULT_VIDEO_CAPS = {
  startFrame: false, endFrame: false, imageReference: false, videoReference: false,
  durations: [5, 10], defaultDuration: 5,
  defaultResolution: "720p", defaultAspectRatio: "16:9",
};

const DEFAULT_IMAGE_CAPS = {
  apiVersion: "v2",
  resolutionTiers: { SMALL: null, MEDIUM: { base: 1024 }, LARGE: null },
  defaultTier: "MEDIUM",
  qualityOptions: null, defaultQuality: null,
  imageReference: true, refMethod: "guidances",
  maxRefs: 6, strengthSupported: true, defaultStrength: "MID",
};

// Build default model entries from providerModels.js (the source of truth for routing)
function buildDefaultModels() {
  try {
    const raw = getProviderModels("leonardo");
    return raw.map(m => ({
      id: m.id,
      name: m.name || m.id,
      uuid: m.uuid || "",
      type: m.type || "image",
      apiModelName: m.apiModelName || m.id.replace(/^leo-/, ""),
      quality: "",
      isPublic: true,
      promptEnhance: "OFF",
      dimensions: {},
      capabilities: m.type === "video" ? { ...DEFAULT_VIDEO_CAPS } : { ...DEFAULT_IMAGE_CAPS },
    }));
  } catch {
    return [];
  }
}

async function getConfig() {
  const saved = await leoKv.get(CONFIG_KEY, null);
  if (saved && Array.isArray(saved.models)) return saved;
  // First-time: seed from providerModels.js
  return {
    failThreshold: 3,
    coolingMinutes: 60,
    autoDisableThreshold: 5,
    imageTimeoutMs: 120000,
    videoTimeoutMs: 600000,
    models: buildDefaultModels(),
  };
}

export const dynamic = "force-dynamic";

export async function GET(req, res) {
  try {
    const config = await getConfig();
    return res.json({ ok: true, config });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}

export async function PUT_handler(req, res) {
  try {
    const body = req.body;
    // Deduplicate models by id
    if (Array.isArray(body.models)) {
      const seen = new Set();
      body.models = body.models.filter(m => {
        if (!m.id || seen.has(m.id)) return false;
        seen.add(m.id);
        return true;
      });
    }
    await leoKv.set(CONFIG_KEY, body);
    return res.json({ ok: true });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}

export async function POST_handler(req, res) {
  try {
    const body = req.body;
    const { action } = body;

    // ── Add single model ─────────────────────────────────────────────
    if (action === "add-model") {
      const config = await getConfig();
      const model = body.model;
      if (!model?.id || !model?.name) {
        return res.status(400).json({ error: "model.id dan model.name wajib diisi" });
      }
      if (!model.id.startsWith("leo-")) model.id = `leo-${model.id}`;
      if (config.models.some(m => m.id === model.id)) {
        return res.status(409).json({ error: `Model "${model.id}" sudah ada` });
      }
      config.models.push(model);
      await leoKv.set(CONFIG_KEY, config);
      return res.json({ ok: true, model });
    }

    // ── Update single model ──────────────────────────────────────────
    if (action === "update-model") {
      const config = await getConfig();
      const idx = config.models.findIndex(m => m.id === body.id);
      if (idx === -1) return res.status(404).json({ error: "Model tidak ditemukan" });
      config.models[idx] = { ...config.models[idx], ...body.model };
      await leoKv.set(CONFIG_KEY, config);
      return res.json({ ok: true, model: config.models[idx] });
    }

    // ── Delete single model ──────────────────────────────────────────
    if (action === "delete-model") {
      const config = await getConfig();
      const before = config.models.length;
      config.models = config.models.filter(m => m.id !== body.id);
      if (config.models.length === before) {
        return res.status(404).json({ error: "Model tidak ditemukan" });
      }
      await leoKv.set(CONFIG_KEY, config);
      return res.json({ ok: true });
    }

    // ── Import all default models from providerModels.js ─────────────
    if (action === "import-defaults") {
      const config = await getConfig();
      const defaults = buildDefaultModels();
      const existingIds = new Set(config.models.map(m => m.id));
      const added = defaults.filter(m => !existingIds.has(m.id));
      config.models = [...config.models, ...added];
      await leoKv.set(CONFIG_KEY, config);
      return res.json({
        ok: true,
        added: added.length,
        imageAdded: added.filter(m => m.type === "image").length,
        videoAdded: added.filter(m => m.type === "video").length,
      });
    }

    // ── Delete all models ────────────────────────────────────────────
    if (action === "delete-all-models") {
      const config = await getConfig();
      const count = config.models.length;
      config.models = [];
      await leoKv.set(CONFIG_KEY, config);
      return res.json({ ok: true, deleted: count });
    }

    // ── Save global settings only (not models) ───────────────────────
    if (action === "save-settings") {
      const config = await getConfig();
      const { failThreshold, coolingMinutes, autoDisableThreshold, imageTimeoutMs, videoTimeoutMs } = body;
      if (failThreshold !== undefined) config.failThreshold = Number(failThreshold);
      if (coolingMinutes !== undefined) config.coolingMinutes = Number(coolingMinutes);
      if (autoDisableThreshold !== undefined) config.autoDisableThreshold = Number(autoDisableThreshold);
      if (imageTimeoutMs !== undefined) config.imageTimeoutMs = Number(imageTimeoutMs);
      if (videoTimeoutMs !== undefined) config.videoTimeoutMs = Number(videoTimeoutMs);
      await leoKv.set(CONFIG_KEY, config);
      return res.json({ ok: true });
    }

    return res.status(400).json({ error: "Unknown action" });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
