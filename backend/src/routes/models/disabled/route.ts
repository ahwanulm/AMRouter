
import { getDisabledModels, disableModels, enableModels } from "../../../lib/disabledModelsDb.js";

export const dynamic = "force-dynamic";

// GET /api/models/disabled?providerAlias=xxx
export async function GET_handler(req, res) {
  try {
    const { searchParams } = new URL('http://localhost' + req.originalUrl);
    const providerAlias = searchParams.get("providerAlias");
    const all = await getDisabledModels();
    if (providerAlias) return res.json({ ids: all[providerAlias] || [] });
    return res.json({ disabled: all });
  } catch (error) {
    console.log("Error fetching disabled models:", error);
    return res.status(500).json({ error: "Failed to fetch disabled models" });
  }
}

// POST /api/models/disabled  body: { providerAlias, ids: [...] }
export async function POST_handler(req, res) {
  try {
    const { providerAlias, ids } = req.body;
    if (!providerAlias || !Array.isArray(ids)) {
      return res.status(400).json({ error: "providerAlias and ids[] required" });
    }
    await disableModels(providerAlias, ids);
    return res.json({ success: true });
  } catch (error) {
    console.log("Error disabling models:", error);
    return res.status(500).json({ error: "Failed to disable models" });
  }
}

// DELETE /api/models/disabled?providerAlias=xxx[&id=yyy]
export async function DELETE_handler(req, res) {
  try {
    const { searchParams } = new URL('http://localhost' + req.originalUrl);
    const providerAlias = searchParams.get("providerAlias");
    const id = searchParams.get("id");
    if (!providerAlias) {
      return res.status(400).json({ error: "providerAlias required" });
    }
    await enableModels(providerAlias, id ? [id] : []);
    return res.json({ success: true });
  } catch (error) {
    console.log("Error enabling models:", error);
    return res.status(500).json({ error: "Failed to enable models" });
  }
}
