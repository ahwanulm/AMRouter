import { handleStt } from "../../../../sse/handlers/stt.js";

// Allow large audio uploads — 5min for processing large files
export const maxDuration = 300;

export async function OPTIONS() {
  return new Response(null, {
    headers: {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers": "*",
    },
  });
}

import { Readable } from "node:stream";

/** POST /v1/audio/transcriptions - OpenAI Whisper compatible STT */
export async function POST_handler(req, res) {
  const fullUrl = `${req.protocol}://${req.get('host')}${req.originalUrl}`;
  const webReq = new Request(fullUrl, {
    method: req.method,
    headers: new Headers(req.headers),
    body: req.method !== 'GET' && req.method !== 'HEAD' ? Readable.toWeb(req) : undefined,
    duplex: 'half'
  });
  return await handleStt(webReq);
}
