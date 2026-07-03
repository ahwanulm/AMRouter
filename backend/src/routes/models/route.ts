
import { getModelAliases, setModelAlias } from "../../models/index.js";
import { getDisabledModels } from "../../lib/disabledModelsDb.js";
import { AI_MODELS } from "../../shared/constants/config.js";
import { getProviderAlias } from "../../shared/constants/providers.js";

// GET /api/models - Get models with aliases
export async function GET(req, res) {
  try {
    const modelAliases = await getModelAliases();
    const disabled = await getDisabledModels();

    const models = AI_MODELS
      .filter((m) => {
        const alias = getProviderAlias(m.provider) || m.provider;
        const list = disabled[alias] || disabled[m.provider] || [];
        return !list.includes(m.model);
      })
      .map((m) => {
        const fullModel = `${m.provider}/${m.model}`;
        return {
          ...m,
          fullModel,
          alias: modelAliases[fullModel] || m.model,
        };
      });

    return res.json({ models });
  } catch (error) {
    console.log("Error fetching models:", error);
    return res.status(500).json({ error: "Failed to fetch models" });
  }
}

// PUT /api/models - Update model alias
export async function PUT_handler(req, res) {
  try {
    const body = req.body;
    const { model, alias } = body;

    if (!model || !alias) {
      return res.status(400).json({ error: "Model and alias required" });
    }

    const modelAliases = await getModelAliases();

    // Check if alias already exists for different model
    const existingModel = Object.entries(modelAliases).find(
      ([key, val]) => val === alias && key !== model
    );

    if (existingModel) {
      return res.status(400).json({ error: "Alias already in use" });
    }

    // Update alias
    await setModelAlias(model, alias);

    return res.json({ success: true, model, alias });
  } catch (error) {
    console.log("Error updating alias:", error);
    return res.status(500).json({ error: "Failed to update alias" });
  }
}
