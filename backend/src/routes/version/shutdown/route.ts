
import { killAppProcesses } from "../../../lib/appUpdater.js";

// Shutdown app to release file locks for manual update
export async function POST(req, res) {
  try {
    await killAppProcesses();
  } catch { /* best effort */ }

  const response = res.json({ success: true, message: "Shutting down for manual update..." });

  setTimeout(() => process.exit(0), 500);

  return response;
}
