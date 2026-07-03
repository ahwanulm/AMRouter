
import { deleteProviderConnectionsByProvider, deleteProviderNode, getProviderConnections, getProviderNodeById, updateProviderConnection, updateProviderNode } from "../../../models/index.js";

// PUT /api/provider-nodes/[id] - Update provider node
export async function PUT_handler(req, res, { params }) {
  try {
    const { id } = await params;
    const body = req.body;
    const { name, prefix, apiType, baseUrl } = body;
    const node = await getProviderNodeById(id);

    if (!node) {
      return res.status(404).json({ error: "Provider node not found" });
    }

    if (!name?.trim()) {
      return res.status(400).json({ error: "Name is required" });
    }

    if (!prefix?.trim()) {
      return res.status(400).json({ error: "Prefix is required" });
    }

    // Only validate apiType for OpenAI Compatible nodes
    if (node.type === "openai-compatible" && (!apiType || !["chat", "responses"].includes(apiType))) {
      return res.status(400).json({ error: "Invalid OpenAI compatible API type" });
    }

    if (!baseUrl?.trim()) {
      return res.status(400).json({ error: "Base URL is required" });
    }

    let sanitizedBaseUrl = baseUrl.trim();
    
    // Sanitize Base URL for Anthropic Compatible
    if (node.type === "anthropic-compatible") {
      sanitizedBaseUrl = sanitizedBaseUrl.replace(/\/$/, "");
      if (sanitizedBaseUrl.endsWith("/messages")) {
        sanitizedBaseUrl = sanitizedBaseUrl.slice(0, -9); // remove /messages
      }
    }

    // Sanitize Base URL for Custom Embedding (strip trailing slash and /embeddings)
    if (node.type === "custom-embedding") {
      sanitizedBaseUrl = sanitizedBaseUrl.replace(/\/$/, "");
      if (sanitizedBaseUrl.endsWith("/embeddings")) {
        sanitizedBaseUrl = sanitizedBaseUrl.slice(0, -"/embeddings".length);
      }
    }

    const updates = {
      name: name.trim(),
      prefix: prefix.trim(),
      baseUrl: sanitizedBaseUrl,
    };

    if (node.type === "openai-compatible") {
      updates.apiType = apiType;
    }

    const updated = await updateProviderNode(id, updates);

    const connections = await getProviderConnections({ provider: id });
    await Promise.all(connections.map((connection) => (
      updateProviderConnection(connection.id, {
        providerSpecificData: {
          ...(connection.providerSpecificData || {}),
          prefix: prefix.trim(),
          apiType: node.type === "openai-compatible" ? apiType : undefined,
          baseUrl: sanitizedBaseUrl,
          nodeName: updated.name,
        }
      })
    )));

    return res.json({ node: updated });
  } catch (error) {
    console.log("Error updating provider node:", error);
    return res.status(500).json({ error: "Failed to update provider node" });
  }
}

// DELETE /api/provider-nodes/[id] - Delete provider node and its connections
export async function DELETE_handler(req, res, { params }) {
  try {
    const { id } = await params;
    const node = await getProviderNodeById(id);

    if (!node) {
      return res.status(404).json({ error: "Provider node not found" });
    }

    await deleteProviderConnectionsByProvider(id);
    await deleteProviderNode(id);

    return res.json({ success: true });
  } catch (error) {
    console.log("Error deleting provider node:", error);
    return res.status(500).json({ error: "Failed to delete provider node" });
  }
}
