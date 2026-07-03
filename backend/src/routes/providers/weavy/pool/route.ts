
import { getProviderConnections, updateProviderConnection } from "../../../../models/index.js";

// GET /api/providers/weavy/pool
// Returns all Weavy accounts with JWT + Firebase token data for copy-paste
export async function GET(req, res) {
  try {
    const connections = await getProviderConnections({ provider: "weavy" });

    const now = Math.floor(Date.now() / 1000);
    const pool = connections.map((conn) => {
      const psd = conn.providerSpecificData || {};
      const jwtExpiresAt = conn.jwt_expires_at || conn.jwtExpiresAt || 0;
      const jwt = conn.accessToken || conn.cached_jwt || "";
      const jwtValid = jwt && jwtExpiresAt > now + 60;

      return {
        id: conn.id,
        email: conn.email || conn.name || "",
        isActive: conn.isActive !== false,
        jwt: jwt || "",
        jwtExpiresAt: jwtExpiresAt || 0,
        jwtValid,
        jwtSecondsLeft: jwtExpiresAt ? Math.max(0, jwtExpiresAt - now) : 0,
        firebaseRefreshToken: psd.firebase_refresh_token || "",
        firebaseApiKey: psd.firebase_api_key || "",
        balance: conn.last_balance || psd.last_balance || 150,
        lastError: conn.lastError || "",
        updatedAt: conn.updatedAt || "",
      };
    });

    // Sort: valid JWT first, then by email
    pool.sort((a, b) => {
      if (a.jwtValid !== b.jwtValid) return a.jwtValid ? -1 : 1;
      return (a.email || "").localeCompare(b.email || "");
    });

    const stats = {
      total: pool.length,
      active: pool.filter((a) => a.isActive).length,
      jwtValid: pool.filter((a) => a.jwtValid).length,
      hasRefreshToken: pool.filter((a) => a.firebaseRefreshToken).length,
    };

    return res.json({ ok: true, stats, pool });
  } catch (err) {
    return res.status(500).json({ ok: false, error: err.message });
  }
}
