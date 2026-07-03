
import fs from "fs";

const SCREENSHOT_PATH = "/tmp/9router_debug.png";

// GET: serve the latest debug screenshot or status
export async function GET_handler(req, res) {
  const { searchParams } = new URL('http://localhost' + req.originalUrl);
  const action = searchParams.get("action");

  if (action === "screenshot") {
    try {
      const stat = fs.statSync(SCREENSHOT_PATH);
      // Only serve if file is recent (less than 10s old)
      const age = Date.now() - stat.mtimeMs;
      if (age > 10000) {
        throw new Error("stale");
      }
      const buf = fs.readFileSync(SCREENSHOT_PATH);
      return new Response(buf, {
        headers: {
          "Content-Type": "image/png",
          "Cache-Control": "no-cache, no-store, must-revalidate",
          "Pragma": "no-cache",
          "X-Screenshot-Age": String(Math.round(age)),
        },
      });
    } catch {
      // Return 1x1 transparent pixel if no screenshot available
      const pixel = Buffer.from(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
        "base64"
      );
      return new Response(pixel, {
        headers: { "Content-Type": "image/png", "Cache-Control": "no-cache" },
      });
    }
  }

  // Status: check if screenshot file exists and is recent
  try {
    const stat = fs.statSync(SCREENSHOT_PATH);
    const age = Date.now() - stat.mtimeMs;
    return res.json({
      available: age < 10000,
      age: Math.round(age),
    });
  } catch {
    return res.json({ available: false });
  }
}
