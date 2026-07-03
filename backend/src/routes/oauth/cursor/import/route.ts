
import { CursorService } from "../../../../lib/oauth/services/cursor.js";
import { createProviderConnection } from "../../../../models/index.js";

/**
 * POST /api/oauth/cursor/import
 * Import and validate access token from Cursor IDE's local SQLite database
 *
 * Request body:
 * - accessToken - Access token from cursorAuth/accessToken
 * - machineId - Machine ID from storage.serviceMachineId
 */
export async function POST_handler(req, res) {
  try {
    const { accessToken, machineId } = req.body;

    if (!accessToken || typeof accessToken !== "string") {
      return res.json(
        { error: "Access token is required" },
        { status: 400 }
      );
    }

    if (!machineId || typeof machineId !== "string") {
      return res.json(
        { error: "Machine ID is required" },
        { status: 400 }
      );
    }

    const cursorService = new CursorService();

    // Validate token by making API call
    const tokenData = await cursorService.validateImportToken(
      accessToken.trim(),
      machineId.trim()
    );

    // Try to extract user info from token
    const userInfo = cursorService.extractUserInfo(tokenData.accessToken);

    // Save to database
    const connection = await createProviderConnection({
      provider: "cursor",
      authType: "oauth",
      accessToken: tokenData.accessToken,
      refreshToken: null, // Cursor doesn't have public refresh endpoint
      expiresAt: new Date(Date.now() + tokenData.expiresIn * 1000).toISOString(),
      email: userInfo?.email || null,
      providerSpecificData: {
        machineId: tokenData.machineId,
        authMethod: "imported",
        provider: "Imported",
        userId: userInfo?.userId,
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
    console.log("Cursor import token error:", error);
    return res.status(500).json({ error: error.message });
  }
}

/**
 * GET /api/oauth/cursor/import
 * Get instructions for importing Cursor token
 */
export async function GET(req, res) {
  const cursorService = new CursorService();
  const instructions = cursorService.getTokenStorageInstructions();

  return res.json({
    provider: "cursor",
    method: "import_token",
    instructions,
    requiredFields: [
      {
        name: "accessToken",
        label: "Access Token",
        description: "From cursorAuth/accessToken in state.vscdb",
        type: "textarea",
      },
      {
        name: "machineId",
        label: "Machine ID",
        description: "From storage.serviceMachineId in state.vscdb",
        type: "text",
      },
    ],
  });
}
