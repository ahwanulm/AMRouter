
import { deleteApiKey, getApiKeyById, updateApiKey } from "../../../lib/localDb.js";

// GET /api/keys/[id] - Get single key
export async function GET_handler(req, res, { params }) {
  try {
    const { id } = await params;
    const key = await getApiKeyById(id);
    if (!key) {
      return res.status(404).json({ error: "Key not found" });
    }
    return res.json({ key });
  } catch (error) {
    console.log("Error fetching key:", error);
    return res.status(500).json({ error: "Failed to fetch key" });
  }
}

// PUT /api/keys/[id] - Update key
export async function PUT_handler(req, res, { params }) {
  try {
    const { id } = await params;
    const body = req.body;
    const { isActive } = body;

    const existing = await getApiKeyById(id);
    if (!existing) {
      return res.status(404).json({ error: "Key not found" });
    }

    const updateData = {};
    if (isActive !== undefined) updateData.isActive = isActive;

    const updated = await updateApiKey(id, updateData);

    return res.json({ key: updated });
  } catch (error) {
    console.log("Error updating key:", error);
    return res.status(500).json({ error: "Failed to update key" });
  }
}

// DELETE /api/keys/[id] - Delete API key
export async function DELETE_handler(req, res, { params }) {
  try {
    const { id } = await params;

    const deleted = await deleteApiKey(id);
    if (!deleted) {
      return res.status(404).json({ error: "Key not found" });
    }

    return res.json({ message: "Key deleted successfully" });
  } catch (error) {
    console.log("Error deleting key:", error);
    return res.status(500).json({ error: "Failed to delete key" });
  }
}
