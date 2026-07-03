
import { getProviderConnections } from "../../../../../lib/localDb.js";

const langNames = new Intl.DisplayNames(["en"], { type: "language" });

/**
 * GET /api/media-providers/tts/inworld/voices[?lang=en]
 * Returns { languages, byLang } grouped by language code (same shape as edge-tts/elevenlabs)
 */
export async function GET_handler(req, res) {
  try {
    const { searchParams } = new URL('http://localhost' + req.originalUrl);
    const langFilter = searchParams.get("lang");

    const connections = await getProviderConnections({ provider: "inworld", isActive: true });
    const apiKey = connections[0]?.apiKey;
    if (!apiKey) return res.status(400).json({ error: "No Inworld connection found" });

    const res = await fetch("https://api.inworld.ai/tts/v1/voices", {
      headers: { "Authorization": `Basic ${apiKey}` },
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      return res.status(502).json({ error: `Inworld API ${res.status}: ${text || "Failed"}` });
    }
    const data = await res.json();
    const voices = data.voices || [];

    const byLang = {};
    for (const v of voices) {
      // Each voice has `languages: ["en", "es", ...]`
      const langs = Array.isArray(v.languages) && v.languages.length ? v.languages : ["en"];
      for (const code of langs) {
        if (!byLang[code]) {
          byLang[code] = {
            code,
            name: (() => { try { return langNames.of(code); } catch { return code; } })(),
            voices: [],
          };
        }
        if (!byLang[code].voices.find((x) => x.id === v.voiceId)) {
          byLang[code].voices.push({
            id: v.voiceId,
            name: v.displayName || v.voiceId,
            gender: v.gender || "",
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
