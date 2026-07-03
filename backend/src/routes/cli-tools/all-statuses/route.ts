"use server";


import { GET as antigravityGet } from "../antigravity-mitm/route.js";
import { GET as claudeGet } from "../claude-settings/route.js";
import { GET as codexGet } from "../codex-settings/route.js";
import { GET as opencodeGet } from "../opencode-settings/route.js";
import { GET as droidGet } from "../droid-settings/route.js";
import { GET as openclawGet } from "../openclaw-settings/route.js";
import { GET as hermesGet } from "../hermes-settings/route.js";
import { GET as coworkGet } from "../cowork-settings/route.js";
import { GET as copilotGet } from "../copilot-settings/route.js";
import { GET as clineGet } from "../cline-settings/route.js";
import { GET as kiloGet } from "../kilo-settings/route.js";
import { GET as deepseekTuiGet } from "../deepseek-tui-settings/route.js";
import { GET as jcodeGet } from "../jcode-settings/route.js";

const STATUS_GETTERS = {
  antigravity: antigravityGet,
  claude: claudeGet,
  codex: codexGet,
  opencode: opencodeGet,
  droid: droidGet,
  openclaw: openclawGet,
  hermes: hermesGet,
  cowork: coworkGet,
  copilot: copilotGet,
  cline: clineGet,
  kilo: kiloGet,
  "deepseek-tui": deepseekTuiGet,
  jcode: jcodeGet,
};

// Batch endpoint: gather all CLI tool statuses in one round-trip
export async function GET(req, res) {
  const entries = await Promise.all(
    Object.entries(STATUS_GETTERS).map(async ([toolId, getter]) => {
      try {
        let resultData = null;
        
        // Mock res object to capture data from Express-style handlers
        const mockRes = {
          json: (data) => { 
            resultData = data; 
            return { json: () => data }; // compatibility for .json().json()
          },
          status: function() { return this; }
        };

        const maybeRes = await (getter)(req, mockRes);
        
        // If the getter returned a standard Web Response instead of using mockRes
        if (maybeRes && typeof maybeRes.json === 'function' && !resultData) {
          resultData = await maybeRes.json();
        }

        return [toolId, resultData];
      } catch (err) {
        console.error(`Error fetching status for ${toolId}:`, err);
        return [toolId, { installed: false, error: true }];
      }
    })
  );
  return res.json(Object.fromEntries(entries));
}
