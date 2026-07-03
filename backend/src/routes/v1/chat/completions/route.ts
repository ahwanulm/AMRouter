import { handleChat } from "../../../../sse/handlers/chat.js";
import { initTranslators } from "../../../../../open-sse/translator/index.js";

// Force compile reload for Kimi Coding header change
let initialized = false;

/**
 * Initialize translators once
 */
async function ensureInitialized() {
  if (!initialized) {
    await initTranslators();
    initialized = true;
  }
}

/**
 * Handle CORS preflight
 */
export async function OPTIONS() {
  return new Response(null, {
    headers: {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "*"
    }
  });
}

export async function POST_handler(req, res) {  
  // Fallback to local handling
  await ensureInitialized();
  
  const fullUrl = `${req.protocol}://${req.get('host')}${req.originalUrl}`;
  const webReq = new Request(fullUrl, {
    method: req.method,
    headers: new Headers(req.headers),
    body: req.method !== 'GET' && req.method !== 'HEAD' ? JSON.stringify(req.body) : undefined
  });
  
  return await handleChat(webReq);
}

