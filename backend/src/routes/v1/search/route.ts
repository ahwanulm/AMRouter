import { handleSearch } from "../../../sse/handlers/search.js";

/**
 * Handle CORS preflight
 */
export async function OPTIONS() {
  return new Response(null, {
    headers: {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers": "*"
    }
  });
}

/**
 * POST /v1/search - Web search endpoint
 */
export async function POST_handler(req, res) {
  const fullUrl = `${req.protocol}://${req.get('host')}${req.originalUrl}`;
  const webReq = new Request(fullUrl, {
    method: req.method,
    headers: new Headers(req.headers),
    body: req.method !== 'GET' && req.method !== 'HEAD' ? JSON.stringify(req.body) : undefined
  });
  return await handleSearch(webReq);
}
