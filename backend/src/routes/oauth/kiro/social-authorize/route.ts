
import { generatePKCE } from "../../../../lib/oauth/utils/pkce.js";
import { KiroService } from "../../../../lib/oauth/services/kiro.js";

/**
 * GET /api/oauth/kiro/social-authorize
 * Generate Google/GitHub social login URL for manual callback flow
 * Uses kiro:// custom protocol as required by AWS Cognito
 */
export async function GET_handler(req, res) {
  try {
    const { searchParams } = new URL('http://localhost' + req.originalUrl);
    const provider = searchParams.get("provider"); // "google" or "github"

    if (!provider || !["google", "github"].includes(provider)) {
      return res.json(
        { error: "Invalid provider. Use 'google' or 'github'" },
        { status: 400 }
      );
    }

    // Generate PKCE for social auth
    const { codeVerifier, codeChallenge, state } = generatePKCE();

    const kiroService = new KiroService();
    const authUrl = kiroService.buildSocialLoginUrl(
      provider,
      codeChallenge,
      state
    );

    return res.json({
      authUrl,
      state,
      codeVerifier,
      codeChallenge,
      provider,
    });
  } catch (error) {
    console.log("Kiro social authorize error:", error);
    return res.status(500).json({ error: error.message });
  }
}
