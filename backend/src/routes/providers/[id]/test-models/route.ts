
import { getProviderConnectionById } from "../../../../lib/localDb.js";
import { getProviderModels, PROVIDER_ID_TO_ALIAS } from "../../../../../open-sse/config/providerModels.js";
import { isOpenAICompatibleProvider, isAnthropicCompatibleProvider } from "../../../../shared/constants/providers.js";
import { UPDATER_CONFIG } from "../../../../shared/constants/config.js";
import { pingModelByKind } from "../../../models/test/ping.js";

/**
 * POST /api/providers/[id]/test-models
 * id = connectionId — used only to resolve provider + model list.
 * Actual requests go through the internal endpoint that matches each model kind.
 */
export async function POST_handler(req, res, { params }) {
  try {
    const { id } = await params;
    const connection = await getProviderConnectionById(id);
    if (!connection) {
      return res.status(404).json({ error: "Connection not found" });
    }

    const providerId = connection.provider;
    const isCompatible = isOpenAICompatibleProvider(providerId) || isAnthropicCompatibleProvider(providerId);
    const alias = PROVIDER_ID_TO_ALIAS[providerId] || providerId;

    let models = getProviderModels(alias);

    const baseUrl = `http://127.0.0.1:${process.env.PORT || 3001}`;

    // Compatible providers: fetch live model list
    if (isCompatible && models.length === 0) {
      try {
        const modelsRes = await fetch(`${baseUrl}/api/providers/${id}/models`);
        if (modelsRes.ok) {
          const data = await modelsRes.json();
          models = (data.models || []).map((m) => ({ id: m.id || m.name, name: m.name || m.id }));
        }
      } catch { /* fallback to empty */ }
    }

    if (models.length === 0) {
      return res.status(400).json({ error: "No models configured for this provider" });
    }

    // Warm up with first model to trigger token refresh (if needed) before parallel calls.
    // This prevents race condition where multiple requests concurrently refresh the same token.
    const [first, ...rest] = models;
    const firstKind = first.type || "llm";
    const firstResult = await pingModelByKind(`${alias}/${first.id}`, firstKind, baseUrl);
    const results = [{ modelId: first.id, name: first.name || first.id, ...firstResult }];

    if (rest.length > 0) {
      const restResults = await Promise.all(
        rest.map(async (model) => {
          const result = await pingModelByKind(`${alias}/${model.id}`, model.type || "llm", baseUrl);
          return { modelId: model.id, name: model.name || model.id, ...result };
        })
      );
      results.push(...restResults);
    }

    return res.json({ provider: providerId, connectionId: id, results });
  } catch (error) {
    console.log("Error testing models:", error);
    return res.status(500).json({ error: "Test failed" });
  }
}
