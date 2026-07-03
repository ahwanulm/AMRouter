
import { getProviderConnections } from "../../../../../lib/localDb.js";

const MINIMAX_VOICE_ENDPOINTS = {
  minimax: "https://api.minimax.io/v1/get_voice",
  "minimax-cn": "https://api.minimaxi.com/v1/get_voice",
};

const VOICE_GROUPS = [
  { key: "system_voice", label: "System" },
  { key: "voice_cloning", label: "Cloned" },
  { key: "voice_generation", label: "Generated" },
  { key: "music_generation", label: "Music" },
];

function inferLanguage(voiceId) {
  const value = typeof voiceId === "string" ? voiceId.trim() : "";
  if (!value.includes("_")) return "Custom";
  return value.split("_")[0] || "Custom";
}

function addVoice(byLang, code, voice) {
  if (!byLang[code]) byLang[code] = { code, name: code, voices: [] };
  if (byLang[code].voices.some((v) => v.id === voice.id)) return;
  byLang[code].voices.push(voice);
}

function normalizeMiniMaxVoices(data) {
  const byLang = {};

  for (const group of VOICE_GROUPS) {
    const voices = Array.isArray(data?.[group.key]) ? data[group.key] : [];
    for (const item of voices) {
      const voiceId = item?.voice_id || item?.voiceId;
      if (!voiceId) continue;

      const voiceName = item?.voice_name || item?.voiceName || voiceId;
      const lang = group.key === "system_voice" ? inferLanguage(voiceId) : "Custom";
      addVoice(byLang, lang, {
        id: voiceId,
        name: group.key === "system_voice" ? voiceName : `${voiceName} · ${group.label}`,
        lang,
        category: group.key,
      });
    }
  }

  const languages = Object.values(byLang).sort((a, b) => {
    if (a.code === "Custom") return 1;
    if (b.code === "Custom") return -1;
    return a.name.localeCompare(b.name);
  });

  for (const lang of languages) {
    lang.voices.sort((a, b) => a.name.localeCompare(b.name));
  }

  return { languages, byLang };
}

/**
 * GET /api/media-providers/tts/minimax/voices[?provider=minimax|minimax-cn&voice_type=all]
 * Returns { languages, byLang } grouped for the shared TTS voice picker.
 */
export async function GET_handler(req, res) {
  try {
    const { searchParams } = new URL('http://localhost' + req.originalUrl);
    const provider = searchParams.get("provider") === "minimax-cn" ? "minimax-cn" : "minimax";
    const voiceType = searchParams.get("voice_type") || "all";
    const langFilter = searchParams.get("lang");

    const connections = await getProviderConnections({ provider, isActive: true });
    const apiKey = connections[0]?.apiKey;
    if (!apiKey) {
      return res.status(400).json({ error: `No ${provider} connection found` });
    }

    const res = await fetch(MINIMAX_VOICE_ENDPOINTS[provider], {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ voice_type: voiceType }),
    });

    const rawText = await res.text();
    let data = {};
    if (rawText) {
      try { data = JSON.parse(rawText); } catch { data = {}; }
    }

    const baseResp = data.base_resp || data.baseResp || {};
    const statusCode = Number(baseResp.status_code ?? baseResp.statusCode ?? 0);
    const statusMessage = baseResp.status_msg || baseResp.statusMsg || data.message || "";

    if (!res.ok) {
      return res.status(502).json({ error: `MiniMax API ${res.status}: ${statusMessage || rawText || "Failed"}` });
    }
    if (statusCode !== 0) {
      return res.status(502).json({ error: statusMessage || "MiniMax voice API error" });
    }

    const normalized = normalizeMiniMaxVoices(data);
    if (langFilter) {
      return res.json({ voices: normalized.byLang[langFilter]?.voices || [] });
    }

    return res.json(normalized);
  } catch (err) {
    return res.status(502).json({ error: err.message || "Failed to fetch MiniMax voices" });
  }
}
