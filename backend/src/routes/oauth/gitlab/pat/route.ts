
import { createProviderConnection } from "../../../../models/index.js";

const GITLAB_DEFAULT_BASE = "https://gitlab.com";

/**
 * POST /api/oauth/gitlab/pat
 * Authenticate GitLab Duo with a Personal Access Token (PAT)
 */
export async function POST_handler(req, res) {
  try {
    let body;
    try {
      body = req.body;
    } catch {
      return res.status(400).json({ error: "Invalid request body" });
    }

    const { token, baseUrl } = body;
    if (!token?.trim()) {
      return res.status(400).json({ error: "Personal Access Token is required" });
    }

    const base = (baseUrl?.trim() || GITLAB_DEFAULT_BASE).replace(/\/$/, "");

    // Verify token by fetching current user
    const userRes = await fetch(`${base}/api/v4/user`, {
      headers: { "Private-Token": token.trim(), Accept: "application/json" },
    });

    if (!userRes.ok) {
      const err = await userRes.text();
      return res.status(401).json({ error: `GitLab token verification failed: ${err}` });
    }

    const user = await userRes.json();
    const email = user.email || user.public_email || "";

    await createProviderConnection({
      provider: "gitlab",
      authType: "oauth",
      accessToken: token.trim(),
      refreshToken: null,
      expiresAt: null,
      email,
      displayName: user.name || user.username || email,
      testStatus: "active",
      providerSpecificData: {
        username: user.username || "",
        email,
        name: user.name || "",
        baseUrl: base,
        authKind: "personal_access_token",
      },
    });

    return res.json({ success: true });
  } catch (error) {
    console.error("GitLab PAT auth error:", error);
    return res.status(500).json({ error: error.message });
  }
}
