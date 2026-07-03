
import { createProviderConnection } from "../../../../models/index.js";

/**
 * iFlow Cookie-Based Authentication
 * POST /api/oauth/iflow/cookie
 * Body: { cookie: "BXAuth=xxx; ..." }
 */
export async function POST_handler(req, res) {
  try {
    const { cookie } = req.body;

    if (!cookie || typeof cookie !== "string") {
      return res.status(400).json({ error: "Cookie is required" });
    }

    // Normalize cookie
    const trimmed = cookie.trim();
    if (!trimmed.includes("BXAuth=")) {
      return res.status(400).json({ error: "Cookie must contain BXAuth field" });
    }

    let normalizedCookie = trimmed;
    if (!normalizedCookie.endsWith(";")) {
      normalizedCookie += ";";
    }

    // Step 1: GET API key info to get the name
    const getResponse = await fetch("https://platform.iflow.cn/api/openapi/apikey", {
      method: "GET",
      headers: {
        "Cookie": normalizedCookie,
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
      },
    });

    if (!getResponse.ok) {
      const errorText = await getResponse.text();
      return res.json(
        { error: `Failed to fetch API key info: ${errorText}` },
        { status: getResponse.status }
      );
    }

    const getResult = await getResponse.json();
    if (!getResult.success) {
      return res.json(
        { error: `API key fetch failed: ${getResult.message}` },
        { status: 400 }
      );
    }

    const keyData = getResult.data;
    if (!keyData.name) {
      return res.status(400).json({ error: "Missing name in API key info" });
    }

    // Step 2: POST to refresh API key
    const postResponse = await fetch("https://platform.iflow.cn/api/openapi/apikey", {
      method: "POST",
      headers: {
        "Cookie": normalizedCookie,
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Origin": "https://platform.iflow.cn",
        "Referer": "https://platform.iflow.cn/",
      },
      body: JSON.stringify({ name: keyData.name }),
    });

    if (!postResponse.ok) {
      const errorText = await postResponse.text();
      return res.json(
        { error: `Failed to refresh API key: ${errorText}` },
        { status: postResponse.status }
      );
    }

    const postResult = await postResponse.json();
    if (!postResult.success) {
      return res.json(
        { error: `API key refresh failed: ${postResult.message}` },
        { status: 400 }
      );
    }

    const refreshedKey = postResult.data;
    if (!refreshedKey.apiKey) {
      return res.status(400).json({ error: "Missing API key in response" });
    }

    // Extract only BXAuth from cookie
    const bxAuthMatch = normalizedCookie.match(/BXAuth=([^;]+)/);
    const bxAuth = bxAuthMatch ? bxAuthMatch[1] : "";
    const cookieToSave = bxAuth ? `BXAuth=${bxAuth};` : "";

    // Save to database
    const connection = await createProviderConnection({
      provider: "iflow",
      authType: "cookie",
      name: refreshedKey.name || keyData.name,
      email: refreshedKey.name || keyData.name,
      apiKey: refreshedKey.apiKey,
      providerSpecificData: {
        cookie: cookieToSave,
        expireTime: refreshedKey.expireTime,
      },
      testStatus: "active",
      isActive: true,
    });

    return res.json({
      success: true,
      connection: {
        id: connection.id,
        provider: connection.provider,
        email: connection.email,
        apiKey: refreshedKey.apiKey.substring(0, 10) + "...", // masked
        expireTime: refreshedKey.expireTime,
      },
    });
  } catch (error) {
    console.error("iFlow cookie auth error:", error);
    return res.status(500).json({ error: error.message });
  }
}
