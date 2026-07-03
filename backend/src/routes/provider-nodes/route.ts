
import { createProviderNode, getProviderNodes } from "../../models/index.js";
import { OPENAI_COMPATIBLE_PREFIX, ANTHROPIC_COMPATIBLE_PREFIX, CUSTOM_EMBEDDING_PREFIX } from "../../shared/constants/providers.js";
import { generateId } from "../../shared/utils/index.js";

export const dynamic = "force-dynamic";

const OPENAI_COMPATIBLE_DEFAULTS = {
  baseUrl: "https://api.openai.com/v1",
};

const ANTHROPIC_COMPATIBLE_DEFAULTS = {
  baseUrl: "https://api.anthropic.com/v1",
};

const CUSTOM_EMBEDDING_DEFAULTS = {
  baseUrl: "https://api.openai.com/v1",
};

// GET /api/provider-nodes - List all provider nodes
export async function GET(req, res) {
  try {
    const nodes = await getProviderNodes();
    return res.json({ nodes });
  } catch (error) {
    console.log("Error fetching provider nodes:", error);
    return res.status(500).json({ error: "Failed to fetch provider nodes" });
  }
}

// POST /api/provider-nodes - Create provider node
export async function POST_handler(req, res) {
  try {
    const body = req.body;
    const { name, prefix, apiType, baseUrl, type } = body;

    if (!name?.trim()) {
      return res.status(400).json({ error: "Name is required" });
    }

    if (!prefix?.trim()) {
      return res.status(400).json({ error: "Prefix is required" });
    }

    // Determine type
    const nodeType = type || "openai-compatible";

    if (nodeType === "openai-compatible") {
      if (!apiType || !["chat", "responses"].includes(apiType)) {
        return res.status(400).json({ error: "Invalid OpenAI compatible API type" });
      }

      const node = await createProviderNode({
        id: `${OPENAI_COMPATIBLE_PREFIX}${apiType}-${generateId()}`,
        type: "openai-compatible",
        prefix: prefix.trim(),
        apiType,
        baseUrl: (baseUrl || OPENAI_COMPATIBLE_DEFAULTS.baseUrl).trim(),
        name: name.trim(),
      });
      return res.status(201).json({ node });
    }

    if (nodeType === "custom-embedding") {
      // Strip trailing slash and /embeddings if user pasted full endpoint
      let sanitizedBaseUrl = (baseUrl || CUSTOM_EMBEDDING_DEFAULTS.baseUrl).trim().replace(/\/$/, "");
      if (sanitizedBaseUrl.endsWith("/embeddings")) {
        sanitizedBaseUrl = sanitizedBaseUrl.slice(0, -"/embeddings".length);
      }

      const node = await createProviderNode({
        id: `${CUSTOM_EMBEDDING_PREFIX}${generateId()}`,
        type: "custom-embedding",
        prefix: prefix.trim(),
        baseUrl: sanitizedBaseUrl,
        name: name.trim(),
      });
      return res.status(201).json({ node });
    }

    if (nodeType === "anthropic-compatible") {
      // Sanitize Base URL: remove trailing slash, and remove trailing /messages if user added it
      // This prevents double-appending /messages at runtime
      let sanitizedBaseUrl = (baseUrl || ANTHROPIC_COMPATIBLE_DEFAULTS.baseUrl).trim().replace(/\/$/, "");
      if (sanitizedBaseUrl.endsWith("/messages")) {
        sanitizedBaseUrl = sanitizedBaseUrl.slice(0, -9); // remove /messages
      }

      const node = await createProviderNode({
        id: `${ANTHROPIC_COMPATIBLE_PREFIX}${generateId()}`,
        type: "anthropic-compatible",
        prefix: prefix.trim(),
        baseUrl: sanitizedBaseUrl,
        name: name.trim(),
      });
      return res.status(201).json({ node });
    }

    return res.status(400).json({ error: "Invalid provider node type" });
  } catch (error) {
    console.log("Error creating provider node:", error);
    return res.status(500).json({ error: "Failed to create provider node" });
  }
}
