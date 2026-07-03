
import fs from "fs";
import path from "path";

export async function GET_handler(req, res) {
  try {
    const { searchParams } = new URL('http://localhost' + req.originalUrl);
    const file = searchParams.get("file");

    if (!file) {
      return res.status(400).json({ success: false, error: "File parameter required" });
    }

    // Security: only allow specific filenames
    const allowedFiles = [
      "1_req_client.json",
      "2_req_source.json",
      "3_req_openai.json",
      "4_req_target.json",
      "5_res_provider.txt",
      "6_res_openai.txt",
      "7_res_client.txt",
      "7_res_client.json",
    ];

    if (!allowedFiles.includes(file)) {
      return res.status(400).json({ success: false, error: "Invalid file name" });
    }

    const logsDir = path.join(process.cwd(), "logs", "translator");
    const filePath = path.join(logsDir, file);

    // Check if file exists
    if (!fs.existsSync(filePath)) {
      return res.status(404).json({ success: false, error: "File not found" });
    }

    const content = fs.readFileSync(filePath, "utf-8");

    return res.json({ success: true, content });
  } catch (error) {
    console.error("Error loading file:", error);
    return res.status(500).json({ success: false, error: error.message });
  }
}
