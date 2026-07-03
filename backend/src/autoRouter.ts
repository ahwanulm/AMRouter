/**
 * Auto-router: scans src/routes/** and mounts each route.ts as an Express endpoint.
 * Converts Next.js App Router conventions (GET/POST/etc exports) to Express handlers.
 *
 * Route convention:
 *   src/routes/providers/[id]/route.ts  → GET/POST /api/providers/:id
 *   src/routes/v1/chat/completions/route.ts → /api/v1/chat/completions (then remapped to /v1)
 */
import { Router, Request, Response } from "express";
import { readdirSync, statSync } from "fs";
import { join, relative } from "path";
import { fileURLToPath, pathToFileURL } from "url";
import { dirname } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROUTES_DIR = join(__dirname, "routes");

const HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"] as const;

/**
 * Convert Next.js dynamic segments [id] → Express :id
 */
function nextToExpress(segment: string): string {
  return segment.replace(/\[([^\]]+)\]/g, ":$1"); // [id] → :id only
}

/**
 * Build an Express-compatible regex path for catch-all routes
 * e.g. "v1beta/models/[...path]" → /^\/v1beta\/models\/(.*)$/ 
 */
function hasCatchAll(pathPart: string): boolean {
  return /\[\.\.\./.test(pathPart);
}

function toCatchAllRegex(pathPart: string): RegExp {
  const base = pathPart
    .replace(/\[\.\.\.([^\]]+)\]/g, "(.*)")
    .replace(/\[([^\]]+)\]/g, "[^/]+")
    .replace(/\//g, "\\/");
  return new RegExp(`^\/${base}$`);
}

/**
 * Recursively find all route.ts files under a directory
 */
function findRouteFiles(dir: string): string[] {
  const results: string[] = [];
  try {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) {
        results.push(...findRouteFiles(full));
      } else if (entry === "route.ts" || entry === "route.js") {
        results.push(full);
      }
    }
  } catch {}
  return results;
}

function getSegmentPriority(segment: string): number {
  if (segment.includes("[...")) return 3;
  if (segment.includes("[")) return 2;
  return 1;
}

function compareRoutePaths(a: string, b: string): number {
  const aSegs = a.split("/");
  const bSegs = b.split("/");
  const len = Math.min(aSegs.length, bSegs.length);
  for (let i = 0; i < len; i++) {
    const priA = getSegmentPriority(aSegs[i]);
    const priB = getSegmentPriority(bSegs[i]);
    if (priA !== priB) return priA - priB;
    if (aSegs[i] !== bSegs[i]) return aSegs[i].localeCompare(bSegs[i]);
  }
  return bSegs.length - aSegs.length;
}

/**
 * Build and return the Express router with all routes mounted
 */
export async function buildAutoRouter(): Promise<Router> {
  const router = Router();
  const routeFiles = findRouteFiles(ROUTES_DIR);
  routeFiles.sort((a, b) => {
    const relA = relative(ROUTES_DIR, a);
    const relB = relative(ROUTES_DIR, b);
    return compareRoutePaths(relA, relB);
  });
  let mounted = 0;

  for (const file of routeFiles) {
    const rel = relative(ROUTES_DIR, file);
    const pathPart = rel.replace(/\/route\.[jt]s$/, "");
    const isCatchAll = hasCatchAll(pathPart);
    const expressPath: string | RegExp = isCatchAll
      ? toCatchAllRegex(pathPart)
      : "/" + pathPart.split("/").map(nextToExpress).join("/");

    let mod: Record<string, unknown>;
    try {
      console.log(`[router] Attempting to import: ${file}`);
      mod = await import(pathToFileURL(file).href);
    } catch (err) {
      console.warn(`[router] Failed to import ${rel}:`, err);
      continue;
    }

    for (const method of HTTP_METHODS) {
      // Support both migration-renamed (GET_handler) and original Next.js exports (GET)
      const handler = (mod[`${method}_handler`] ?? mod[method]) as ((req: Request, res: Response) => Promise<unknown>) | undefined;
      if (typeof handler === "function") {
        const expressMethod = method.toLowerCase() as "get" | "post" | "put" | "patch" | "delete";
        if (expressPath === "/auth/login") {
           console.log(`DEBUG: Mounted ${method} ${expressPath}`);
        }
        router[expressMethod](expressPath, async (req: Request, res: Response) => {
          try {
            const params: Record<string, any> = { ...req.params };
            if (isCatchAll) {
              const matchedPath = req.params[0] || "";
              params.path = matchedPath ? matchedPath.split("/") : [];
            }
            const result = await (handler as any)(req, res, { params });
            
            // If the handler returned a Web Response object (common in Next.js/open-sse)
            if (result && typeof result.status === 'number' && typeof (result as any).headers?.forEach === 'function') {
              const webRes = result as any;
              res.status(webRes.status);
              
              webRes.headers.forEach((value: string, key: string) => {
                res.setHeader(key, value);
              });
              
              if (webRes.body) {
                // If it's a stream (ReadableStream)
                if (typeof webRes.body.getReader === 'function') {
                  const reader = webRes.body.getReader();
                  let closed = false;
                  
                  req.on('close', () => {
                    closed = true;
                    reader.cancel().catch(() => {});
                  });

                  async function readStream() {
                    try {
                      while (!closed) {
                        const { done, value } = await reader.read();
                        if (done || closed) break;
                        res.write(value);
                      }
                      res.end();
                    } catch (err) {
                      console.error("Stream error:", err);
                      res.end();
                    }
                  }
                  readStream();
                } else {
                  // Buffer or string
                  res.send(await webRes.text());
                }
              } else {
                res.end();
              }
            }
          } catch (err) {
            console.error(`[route] ${method} ${expressPath}:`, err);
            if (!res.headersSent) {
              res.status(500).json({ error: (err as Error).message });
            }
          }
        });
        mounted++;
      }
    }
  }

  if (process.env.DEBUG_ROUTES) {
    console.log('[router] Mounted paths:', routeFiles.map(f => {
      const rel = relative(ROUTES_DIR, f);
      const pp = rel.replace(/\/route\.[jt]s$/, '');
      return '/' + pp.split('/').map(nextToExpress).join('/');
    }));
  }
  console.log(`[router] Mounted ${mounted} handlers from ${routeFiles.length} route files`);
  return router;
}
