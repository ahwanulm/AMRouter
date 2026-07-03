
import { getApiKeys, createApiKey } from "../../lib/localDb.js";
import { getConsistentMachineId } from "../../shared/utils/machineId.js";

export const dynamic = "force-dynamic";

// GET /api/keys - List API keys
export async function GET(req, res) {
  try {
    const keys = await getApiKeys();
    return res.json({ keys });
  } catch (error) {
    console.log("Error fetching keys:", error);
    return res.status(500).json({ error: "Failed to fetch keys" });
  }
}

// POST /api/keys - Create new API key
export async function POST_handler(req, res) {
  try {
    const body = req.body;
    const { name } = body;

    if (!name) {
      return res.status(400).json({ error: "Name is required" });
    }

    // Always get machineId from server
    const machineId = await getConsistentMachineId();
    const apiKey = await createApiKey(name, machineId);

    return res.status(201).json({
      key: apiKey.key,
      name: apiKey.name,
      id: apiKey.id,
      machineId: apiKey.machineId,
    });
  } catch (error) {
    console.log("Error creating key:", error);
    return res.status(500).json({ error: "Failed to create key" });
  }
}
