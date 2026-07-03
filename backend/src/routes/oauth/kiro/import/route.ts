
import { KiroService } from "../../../../lib/oauth/services/kiro.js";
import { createProviderConnection } from "../../../../models/index.js";

/**
 * POST /api/oauth/kiro/import
 * Import and validate refresh token from Kiro IDE
 */
export async function POST_handler(req, res) {
  try {
    const { refreshToken } = req.body;

    if (!refreshToken || typeof refreshToken !== "string") {
      return res.json(
        { error: "Refresh token is required" },
        { status: 400 }
      );
    }

    const kiroService = new KiroService();

    // Validate and refresh token
    const tokenData = await kiroService.validateImportToken(refreshToken.trim());

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
        authMethod: "imported",
        provider: "Imported",
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
    console.log("Kiro import token error:", error);
    return res.status(500).json({ error: error.message });
  }
}
