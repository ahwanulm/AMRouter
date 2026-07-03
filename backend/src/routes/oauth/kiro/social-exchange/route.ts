
import { KiroService } from "../../../../lib/oauth/services/kiro.js";
import { createProviderConnection } from "../../../../models/index.js";

/**
 * POST /api/oauth/kiro/social-exchange
 * Exchange authorization code for tokens (Google/GitHub social login)
 * Callback URL will be in format: kiro://kiro.kiroAgent/authenticate-success?code=XXX&state=YYY
 */
export async function POST_handler(req, res) {
  try {
    const { code, codeVerifier, provider } = req.body;

    if (!code || !codeVerifier) {
      return res.json(
        { error: "Missing required fields" },
        { status: 400 }
      );
    }

    if (!provider || !["google", "github"].includes(provider)) {
      return res.json(
        { error: "Invalid provider" },
        { status: 400 }
      );
    }

    const kiroService = new KiroService();

    // Exchange code for tokens (redirect_uri handled internally)
    const tokenData = await kiroService.exchangeSocialCode(
      code,
      codeVerifier
    );

    // Extract email from JWT if available
    const email = kiroService.extractEmailFromJWT(tokenData.accessToken);

    // Save to database
    const connection = await createProviderConnection({
      provider: "kiro",
      authType: "oauth",
      accessToken: tokenData.accessToken,
      refreshToken: tokenData.refreshToken,
      expiresAt: new Date(Date.now() + tokenData.expiresIn * 1000).toISOString(),
      email: email || null,
      providerSpecificData: {
        profileArn: tokenData.profileArn,
        authMethod: provider, // "google" or "github"
        provider: provider.charAt(0).toUpperCase() + provider.slice(1),
      },
      testStatus: "active",
    });

    return res.json({
      success: true,
      connection: {
        id: connection.id,
        provider: connection.provider,
        email: connection.email,
      },
    });
  } catch (error) {
    console.log("Kiro social exchange error:", error);
    return res.status(500).json({ error: error.message });
  }
}
