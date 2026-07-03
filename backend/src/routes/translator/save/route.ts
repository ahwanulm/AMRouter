
import fs from "fs";
import path from "path";

export async function POST_handler(req, res) {
  try {
    const { file, content } = req.body;

    if (!file || content === undefined) {
      return res.status(400).json({ success: false, error: "File and content required" });
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
    
    // Create directory if not exists
    if (!fs.existsSync(logsDir)) {
      fs.mkdirSync(logsDir, { recursive: true });
    }

    const filePath = path.join(logsDir, file);
    fs.writeFileSync(filePath, content, "utf-8");

    return res.json({ success: true });
  } catch (error) {
    console.error("Error saving file:", error);
    return res.status(500).json({ success: false, error: error.message });
  }
}
