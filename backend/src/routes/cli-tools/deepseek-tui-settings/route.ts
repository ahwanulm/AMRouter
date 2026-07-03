"use server";


import { exec } from "child_process";
import { promisify } from "util";
import fs from "fs/promises";
import path from "path";
import os from "os";

const execAsync = promisify(exec);

const PROVIDER_NAME = "9router";

const getDeepSeekDir = () => path.join(os.homedir(), ".deepseek");
const getDeepSeekConfigPath = () => path.join(getDeepSeekDir(), "config.toml");

// Simple TOML parser for key = "value" and [section] patterns
const parseToml = (content) => {
    const result = {};
    let currentSection = result;

    const lines = content.split(/\r?\n/);
    for (const line of lines) {
        const trimmed = line.trim();
        // Skip empty lines and comments
        if (!trimmed || trimmed.startsWith("#")) continue;

        // Section header: [section] or [section.subsection]
        const sectionMatch = trimmed.match(/^\[([^\]]+)\]$/);
        if (sectionMatch) {
            const sectionName = sectionMatch[1];
            if (!result[sectionName]) result[sectionName] = {};
            currentSection = result[sectionName];
            continue;
        }

        // Key = "value" or key = value
        const keyValueMatch = trimmed.match(/^(\w+)\s*=\s*"([^"]*)"$/);
        if (keyValueMatch) {
            currentSection[keyValueMatch[1]] = keyValueMatch[2];
            continue;
        }

        // Key = value (unquoted)
        const unquotedMatch = trimmed.match(/^(\w+)\s*=\s*(.+)$/);
        if (unquotedMatch) {
            currentSection[unquotedMatch[1]] = unquotedMatch[2].trim();
        }
    }

    return result;
};

// Build TOML config for 9Router (openai provider mode)
const build9RouterConfig = (baseUrl, apiKey, model) => {
    const normalizedBaseUrl = baseUrl.endsWith("/v1") ? baseUrl : `${baseUrl}/v1`;
    return `provider = "openai"

[providers.openai]
base_url = "${normalizedBaseUrl}"
api_key = "${apiKey}"
model = "${model}"
`;
};

// Default DeepSeek config (reset state)
const DEFAULT_CONFIG = `provider = "deepseek"
`;

const checkDeepSeekInstalled = async () => {
    try {
        const isWindows = os.platform() === "win32";
        const command = isWindows ? "where deepseek" : "which deepseek";
        await execAsync(command, { windowsHide: true });
        return true;
    } catch {
        try {
            await fs.access(getDeepSeekConfigPath());
            return true;
        } catch {
            return false;
        }
    }
};

const readConfigToml = async () => {
    try {
        return await fs.readFile(getDeepSeekConfigPath(), "utf-8");
    } catch (error) {
        if (error.code === "ENOENT") return "";
        throw error;
    }
};

// Detect 9Router by checking if provider is "openai" and base_url points to localhost/127.0.0.1
const has9RouterConfig = (config) => {
    if (!config) return false;
    const provider = config.provider;
    if (provider !== "openai") return false;
    const openaiSection = config["providers.openai"];
    if (!openaiSection?.base_url) return false;
    return /localhost|127\.0\.0\.1|0\.0\.0\.0/.test(openaiSection.base_url);
};

export async function GET(req, res) {
    try {
        const installed = await checkDeepSeekInstalled();
        if (!installed) {
            return res.json({ installed: false, settings: null, message: "DeepSeek TUI is not installed" });
        }
        const toml = await readConfigToml();
        const config = parseToml(toml);
        return res.json({
            installed: true,
            settings: config,
            has9Router: has9RouterConfig(config),
            configPath: getDeepSeekConfigPath(),
        });
    } catch (error) {
        console.log("Error checking deepseek-tui settings:", error);
        return res.status(500).json({ error: "Failed to check deepseek-tui settings" });
    }
}

export async function POST_handler(req, res) {
    try {
        const { baseUrl, apiKey, model } = req.body;
        if (!baseUrl || !model) {
            return res.status(400).json({ error: "baseUrl and model are required" });
        }

        const dir = getDeepSeekDir();
        await fs.mkdir(dir, { recursive: true });

        const newConfig = build9RouterConfig(baseUrl, apiKey || "sk_9router", model);
        await fs.writeFile(getDeepSeekConfigPath(), newConfig);

        return res.json({
            success: true,
            message: "DeepSeek TUI settings applied successfully!",
            configPath: getDeepSeekConfigPath(),
        });
    } catch (error) {
        console.log("Error updating deepseek-tui settings:", error);
        return res.status(500).json({ error: "Failed to update deepseek-tui settings" });
    }
}

export async function DELETE(req, res) {
    try {
        const configPath = getDeepSeekConfigPath();
        try {
            await fs.access(configPath);
        } catch {
            return res.json({ success: true, message: "No config file to reset" });
        }

        await fs.writeFile(configPath, DEFAULT_CONFIG);
        return res.json({ success: true, message: `${PROVIDER_NAME} config reset to DeepSeek defaults` });
    } catch (error) {
        console.log("Error resetting deepseek-tui settings:", error);
        return res.status(500).json({ error: "Failed to reset deepseek-tui settings" });
    }
}