
import { getProviderConnections } from "../../../../../lib/localDb.js";

const langNames = new Intl.DisplayNames(["en"], { type: "language" });

/**
 * GET /api/media-providers/tts/deepgram/voices[?lang=en]
 * Returns { languages, byLang } grouped by language code (same shape as edge-tts/elevenlabs/inworld)
 * Each Deepgram voice = one model (canonical_name like "aura-2-thalia-en")
 */
export async function GET_handler(req, res) {
  try {
    const { searchParams } = new URL('http://localhost' + req.originalUrl);
    const langFilter = searchParams.get("lang");

    const connections = await getProviderConnections({ provider: "deepgram", isActive: true });
    const apiKey = connections[0]?.apiKey;
    if (!apiKey) return res.status(400).json({ error: "No Deepgram connection found" });

    const res = await fetch("https://api.deepgram.com/v1/models", {
      headers: { "Authorization": `Token ${apiKey}` },
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      return res.status(502).json({ error: `Deepgram API ${res.status}: ${text || "Failed"}` });
    }
    const data = await res.json();
    const ttsModels = data.tts || [];

    const byLang = {};
    for (const m of ttsModels) {
      // Deepgram returns `languages: ["en"]` or sometimes language inferred from canonical_name suffix
      const langs = Array.isArray(m.languages) && m.languages.length
        ? m.languages
        : [m.canonical_name?.split("-").pop() || "en"];
      for (const code of langs) {
        if (!byLang[code]) {
          byLang[code] = {
            code,
            name: (() => { try { return langNames.of(code); } catch { return code; } })(),
            voices: [],
          };
        }
        const voiceId = m.canonical_name || m.name;
        if (!byLang[code].voices.find((x) => x.id === voiceId)) {
          byLang[code].voices.push({
            id: voiceId,
            name: m.name || voiceId,
            gender: m.metadata?.tags?.find((t) => t === "masculine" || t === "feminine") || "",
            lang: code,
          });
        }
      }
    }

    const languages = Object.values(byLang).sort((a, b) => a.name.localeCompare(b.name));

    if (langFilter) {
      return res.json({ voices: byLang[langFilter]?.voices || [] });
    }
    return res.json({ languages, byLang });
  } catch (err) {
    return res.status(502).json({ error: err.message || "Failed to fetch voices" });
  }
}
