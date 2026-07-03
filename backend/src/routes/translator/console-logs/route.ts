
import { clearConsoleLogs, getConsoleLogs, initConsoleLogCapture } from "../../../lib/consoleLogBuffer.js";

initConsoleLogCapture();

export async function GET(req, res) {
  try {
    const logs = getConsoleLogs();
    return res.json({ success: true, logs });
  } catch (error) {
    console.error("Error getting console logs:", error);
    return res.status(500).json({ success: false, error: error.message });
  }
}

export async function DELETE(req, res) {
  try {
    clearConsoleLogs();
    return res.json({ success: true });
  } catch (error) {
    console.error("Error clearing console logs:", error);
    return res.status(500).json({ success: false, error: error.message });
  }
}
