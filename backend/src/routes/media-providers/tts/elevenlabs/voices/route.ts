
import { getProviderConnections } from "../../../../../lib/localDb.js";
import { fetchElevenLabsVoices } from "../../../../../../open-sse/handlers/ttsCore.js";

const langNames = new Intl.DisplayNames(["en"], { type: "language" });

/**
 * GET /api/media-providers/tts/elevenlabs/voices[?lang=en]
 * Returns { languages, byLang } grouped by language - same format as edge-tts
 * Uses direct DB read (no mutex) to avoid blocking on concurrent TTS requests
 */
export async function GET_handler(req, res) {
  try {
    const { searchParams } = new URL('http://localhost' + req.originalUrl);
    const langFilter = searchParams.get("lang");

    // Direct DB read - bypass auth mutex used for TTS inference
    const connections = await getProviderConnections({ provider: "elevenlabs", isActive: true });
    const apiKey = connections[0]?.apiKey;
    if (!apiKey) {
      return res.status(400).json({ error: "No ElevenLabs connection found" });
    }

    const voices = await fetchElevenLabsVoices(apiKey);

    // Group by all supported languages (verified_languages + labels.language)
    const byLang = {};
    const addToLang = (code, voice) => {
      if (!byLang[code]) {
        byLang[code] = {
          code,
          name: (() => { try { return langNames.of(code); } catch { return code; } })(),
          voices: [],
        };
      }
      // Avoid duplicate voice in same lang
      if (!byLang[code].voices.find((v) => v.id === voice.voice_id)) {
        byLang[code].voices.push({
          id: voice.voice_id,
          name: voice.name,
          gender: voice.labels?.gender || "",
          lang: code,
          // premade voices are free; professional library voices added to account may require paid plan
          free_users_allowed: voice.category === "premade" || voice.is_owner === true
        });
      }
    };
    for (const v of voices) {
      // Add to primary language
      const primaryLang = v.labels?.language || "en";
      addToLang(primaryLang, v);
      // Add to all verified languages
      for (const vl of v.verified_languages || []) {
        if (vl.language && vl.language !== primaryLang) {
          addToLang(vl.language, v);
        }
      }
    }

    const languages = Object.values(byLang).sort((a, b) => a.name.localeCompare(b.name));

    // If lang filter requested, return only that group's voices
    if (langFilter) {
      return res.json({ voices: byLang[langFilter]?.voices || [] });
    }

    return res.json({ languages, byLang });
  } catch (err) {
    return res.status(502).json({ error: err.message || "Failed to fetch voices" });
  }
}
