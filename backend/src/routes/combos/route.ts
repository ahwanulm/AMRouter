
import { getCombos, createCombo, getComboByName } from "../../lib/localDb.js";

export const dynamic = "force-dynamic";

// Validate combo name: only a-z, A-Z, 0-9, -, _
const VALID_NAME_REGEX = /^[a-zA-Z0-9_.\-]+$/;

// GET /api/combos - Get all combos
export async function GET(req, res) {
  try {
    const combos = await getCombos();
    return res.json({ combos });
  } catch (error) {
    console.log("Error fetching combos:", error);
    return res.status(500).json({ error: "Failed to fetch combos" });
  }
}

// POST /api/combos - Create new combo
export async function POST_handler(req, res) {
  try {
    const body = req.body;
    const { name, models, kind } = body;

    if (!name) {
      return res.status(400).json({ error: "Name is required" });
    }

    // Validate name format
    if (!VALID_NAME_REGEX.test(name)) {
      return res.status(400).json({ error: "Name can only contain letters, numbers, -, _ and ." });
    }

    // Check if name already exists
    const existing = await getComboByName(name);
    if (existing) {
      return res.status(400).json({ error: "Combo name already exists" });
    }

    const combo = await createCombo({ name, models: models || [], kind: kind || null });

    return res.status(201).json(combo);
  } catch (error) {
    console.log("Error creating combo:", error);
    return res.status(500).json({ error: "Failed to create combo" });
  }
}
