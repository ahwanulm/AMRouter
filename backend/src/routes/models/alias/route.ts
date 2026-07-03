
import { getModelAliases, setModelAlias, deleteModelAlias } from "../../../models/index.js";

export const dynamic = "force-dynamic";

// GET /api/models/alias - Get all aliases
export async function GET(req, res) {
  try {
    const aliases = await getModelAliases();
    return res.json({ aliases });
  } catch (error) {
    console.log("Error fetching aliases:", error);
    return res.status(500).json({ error: "Failed to fetch aliases" });
  }
}

// PUT /api/models/alias - Set model alias
export async function PUT_handler(req, res) {
  try {
    const body = req.body;
    const { model, alias } = body;

    if (!model || !alias) {
      return res.status(400).json({ error: "Model and alias required" });
    }

    await setModelAlias(alias, model);

    return res.json({ success: true, model, alias });
  } catch (error) {
    console.log("Error updating alias:", error);
    return res.status(500).json({ error: "Failed to update alias" });
  }
}

// DELETE /api/models/alias?alias=xxx - Delete alias
export async function DELETE_handler(req, res) {
  try {
    const { searchParams } = new URL('http://localhost' + req.originalUrl);
    const alias = searchParams.get("alias");

    if (!alias) {
      return res.status(400).json({ error: "Alias required" });
    }

    await deleteModelAlias(alias);

    return res.json({ success: true });
  } catch (error) {
    console.log("Error deleting alias:", error);
    return res.status(500).json({ error: "Failed to delete alias" });
  }
}
