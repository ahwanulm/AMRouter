
import { getCustomModels, addCustomModel, deleteCustomModel } from "../../../models/index.js";

export const dynamic = "force-dynamic";

// GET /api/models/custom - List all custom models
export async function GET(req, res) {
  try {
    const models = await getCustomModels();
    return res.json({ models });
  } catch (error) {
    console.log("Error fetching custom models:", error);
    return res.status(500).json({ error: "Failed to fetch custom models" });
  }
}

// POST /api/models/custom - Add custom model
export async function POST_handler(req, res) {
  try {
    const { providerAlias, id, type, name } = req.body;
    if (!providerAlias || !id) {
      return res.status(400).json({ error: "providerAlias and id required" });
    }
    const added = await addCustomModel({ providerAlias, id, type: type || "llm", name });
    return res.json({ success: true, added });
  } catch (error) {
    console.log("Error adding custom model:", error);
    return res.status(500).json({ error: "Failed to add custom model" });
  }
}

// DELETE /api/models/custom?providerAlias=xxx&id=yyy&type=zzz
export async function DELETE_handler(req, res) {
  try {
    const { searchParams } = new URL('http://localhost' + req.originalUrl);
    const providerAlias = searchParams.get("providerAlias");
    const id = searchParams.get("id");
    const type = searchParams.get("type") || "llm";
    if (!providerAlias || !id) {
      return res.status(400).json({ error: "providerAlias and id required" });
    }
    await deleteCustomModel({ providerAlias, id, type });
    return res.json({ success: true });
  } catch (error) {
    console.log("Error deleting custom model:", error);
    return res.status(500).json({ error: "Failed to delete custom model" });
  }
}
