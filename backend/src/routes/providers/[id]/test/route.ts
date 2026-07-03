
import { testSingleConnection } from "./testUtils.js";

// POST /api/providers/[id]/test - Test connection
export async function POST_handler(req, res, { params }) {
  try {
    const { id } = await params;
    const result = await testSingleConnection(id);

    if (result.error === "Connection not found") {
      return res.status(404).json({ error: "Connection not found" });
    }

    return res.json({
      valid: result.valid,
      error: result.error,
      refreshed: result.refreshed || false,
    });
  } catch (error) {
    console.log("Error testing connection:", error);
    return res.status(500).json({ error: "Test failed" });
  }
}
