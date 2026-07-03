
import { sendToChild, findPlugin } from "../../../../lib/mcp/stdioSseBridge.js";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST_handler(req, res, { params }) {
  // Cowork disabled: MCP stdio bridge spawns arbitrary processes (RCE risk).
  return res.status(403).json({ error: "Cowork is disabled" });
  const { plugin } = await params;
  if (!findPlugin(plugin)) {
    return res.status(404).json({ error: `Unknown plugin: ${plugin}` });
  }
  try {
    const body = req.body;
    sendToChild(plugin, body);
    return new Response(null, { status: 202 });
  } catch (e) {
    return res.status(500).json({ error: e.message });
  }
}
