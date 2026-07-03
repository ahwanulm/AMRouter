
import { detectFormat, getTargetFormat } from "../../../../open-sse/services/provider.js";
import { translateRequest } from "../../../../open-sse/translator/index.js";
import { FORMATS } from "../../../../open-sse/translator/formats.js";
import { parseModel } from "../../../../open-sse/services/model.js";
import { getProviderConnections } from "../../../lib/localDb.js";
import { getExecutor } from "../../../../open-sse/executors/index.js";

export async function POST_handler(req, res) {
  try {
    const { step, body } = req.body;

    if (!step || !body) {
      return res.status(400).json({ success: false, error: "Step and body required" });
    }

    switch (step) {
      case 1: {
        // Detect provider + formats from 1_req_client.json
        const clientBody = body.body || body;
        const { provider, model } = parseModel(clientBody.model);
        const sourceFormat = detectFormat(clientBody);
        const targetFormat = getTargetFormat(provider);
        return res.json({ success: true, result: { provider, model, sourceFormat, targetFormat } });
      }

      case 2: {
        // source → OpenAI intermediate (mirrors 3_req_openai.json)
        // Translate source→openai only (half of the pipeline)
        const clientBody = body.body || body;
        const { provider, model } = parseModel(clientBody.model);
        const sourceFormat = detectFormat(clientBody);
        const stream = clientBody.stream !== false;

        // translateRequest(source, OPENAI) = only the first half
        const result = translateRequest(sourceFormat, FORMATS.OPENAI, model, clientBody, stream, null, provider);
        delete result._toolNameMap;

        return res.json({ success: true, result: { body: result } });
      }

      case 3: {
        // OpenAI intermediate → target + build URL/headers (mirrors 4_req_target.json)
        const openaiBody = body.body || body;
        const provider = body.provider;
        const model = body.model;

        if (!provider || !model) {
          return res.status(400).json({ success: false, error: "provider and model required" });
        }

        const targetFormat = getTargetFormat(provider);
        const stream = openaiBody.stream !== false;

        // translateRequest(OPENAI, target) = second half of pipeline
        const translated = translateRequest(FORMATS.OPENAI, targetFormat, model, openaiBody, stream, null, provider);
        delete translated._toolNameMap;

        // Build URL + headers via executor (same as chatCore → executor.execute)
        const connections = await getProviderConnections({ provider });
        const connection = connections.find(c => c.isActive !== false);
        if (!connection) {
          return res.status(400).json({ success: false, error: `No active connection for provider: ${provider}` });
        }

        const credentials = {
          apiKey: connection.apiKey,
          accessToken: connection.accessToken,
          refreshToken: connection.refreshToken,
          copilotToken: connection.copilotToken,
          projectId: connection.projectId,
          providerSpecificData: connection.providerSpecificData
        };

        const executor = getExecutor(provider);
        const url = executor.buildUrl(model, stream, 0, credentials);
        const headers = executor.buildHeaders(credentials, stream);
        const finalBody = executor.transformRequest(model, translated, stream, credentials);

        return res.json({ success: true, result: { url, headers, body: finalBody } });
      }

      default:
        return res.status(400).json({ success: false, error: "Invalid step (1-3)" });
    }
  } catch (error) {
    console.error("Error in translator:", error);
    return res.status(500).json({ success: false, error: error.message });
  }
}
