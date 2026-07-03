

const KILO_MODELS_URL = "https://api.kilo.ai/api/gateway/models";

// In-memory cache with TTL
let cachedModels = null;
let cacheTimestamp = 0;
const CACHE_TTL_MS = 60 * 60 * 1000; // 1 hour

export async function GET(req, res) {
  const now = Date.now();

  // Return cached result if still valid
  if (cachedModels && now - cacheTimestamp < CACHE_TTL_MS) {
    return res.json({ models: cachedModels, cached: true });
  }

  try {
    const res = await fetch(KILO_MODELS_URL, {
      headers: { "Accept": "application/json" },
      signal: AbortSignal.timeout(10000),
    });

    if (!res.ok) {
      throw new Error(`Kilo API returned ${res.status}`);
    }

    const json = await res.json();
    const allModels = json.data || [];

    const freeModels = allModels
      .filter((m) => m.isFree === true)
      .map((m) => ({
        id: m.id,
        name: m.name,
        isFree: true,
        context_length: m.context_length || 0,
      }));

    cachedModels = freeModels;
    cacheTimestamp = now;

    return res.json({ models: freeModels, cached: false });
  } catch (error) {
    // Return cached data if available, even if expired
    if (cachedModels) {
      return res.json({ models: cachedModels, cached: true, warning: error.message });
    }

    return res.status(502).json(
      { models: [], error: `Failed to fetch Kilo models: ${error.message}` }
    );
  }
}
