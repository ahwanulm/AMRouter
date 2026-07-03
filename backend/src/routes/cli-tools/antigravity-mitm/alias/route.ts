"use server";


import { getMitmAlias, setMitmAliasAll } from "../../../../models/index.js";
import { getMitmStatus } from "../../../../mitm/manager.js";
import { writeAliasForTool } from "../../../../lib/mitmAliasCache.js";

// GET - Get MITM aliases for a tool
export async function GET_handler(req, res) {
  try {
    const { searchParams } = new URL('http://localhost' + req.originalUrl);
    const toolName = searchParams.get("tool");
    const aliases = await getMitmAlias(toolName || undefined);
    return res.json({ aliases });
  } catch (error) {
    console.log("Error fetching MITM aliases:", error.message);
    return res.status(500).json({ error: "Failed to fetch aliases" });
  }
}

// PUT - Save MITM aliases for a specific tool
export async function PUT_handler(req, res) {
  try {
    const { tool, mappings } = req.body;

    if (!tool || !mappings || typeof mappings !== "object") {
      return res.status(400).json({ error: "tool and mappings required" });
    }

    // Check if DNS is enabled for this tool
    const status = await getMitmStatus();
    if (!status.dnsStatus || !status.dnsStatus[tool]) {
      return res.json(
        { error: `DNS must be enabled for ${tool} before editing model mappings` },
        { status: 403 }
      );
    }

    const filtered = {};
    for (const [alias, model] of Object.entries(mappings)) {
      if (model && model.trim()) {
        filtered[alias] = model.trim();
      }
    }

    await setMitmAliasAll(tool, filtered);
    writeAliasForTool(tool, filtered);
    return res.json({ success: true, aliases: filtered });
  } catch (error) {
    console.log("Error saving MITM aliases:", error.message);
    return res.status(500).json({ error: "Failed to save aliases" });
  }
}
