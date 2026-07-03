"use server";


import { exec } from "child_process";
import { promisify } from "util";
import fs from "fs/promises";
import path from "path";
import os from "os";

const execAsync = promisify(exec);

// Get claude settings path based on OS
const getClaudeSettingsPath = () => {
  const homeDir = os.homedir();
  return path.join(homeDir, ".claude", "settings.json");
};


// Check if claude CLI is installed (via which/where or config file exists)
const checkClaudeInstalled = async () => {
  try {
    const isWindows = os.platform() === "win32";
    const command = isWindows ? "where claude" : "which claude";
    const env = isWindows
      ? { ...process.env, PATH: `${process.env.APPDATA}\\npm;${process.env.PATH}` }
      : process.env;
    await execAsync(command, { windowsHide: true, env });
    return true;
  } catch {
    try {
      await fs.access(getClaudeSettingsPath());
      return true;
    } catch {
      return false;
    }
  }
};

// Read current settings
const readSettings = async () => {
  try {
    const settingsPath = getClaudeSettingsPath();
    const content = await fs.readFile(settingsPath, "utf-8");
    return JSON.parse(content);
  } catch (error) {
    if (error.code === "ENOENT") {
      return null;
    }
    throw error;
  }
};

// GET - Check claude CLI and read current settings
export async function GET(req, res) {
  try {
    const isInstalled = await checkClaudeInstalled();
    
    if (!isInstalled) {
      return res.json({
        installed: false,
        settings: null,
        message: "Claude CLI is not installed",
      });
    }

    const settings = await readSettings();
    const baseUrl = settings?.env?.ANTHROPIC_BASE_URL || "";
    
    // Check if the configured URL points to a 9Router instance (local, tunnel, or cloud)
    const is9Router = baseUrl.includes("localhost:3001") || 
                      baseUrl.includes("127.0.0.1:3001") || 
                      baseUrl.includes(".trycloudflare.com") || 
                      baseUrl.includes("9router.com");

    return res.json({
      installed: true,
      settings: settings,
      has9Router: is9Router,
      settingsPath: getClaudeSettingsPath(),
    });
  } catch (error) {
    console.log("Error checking claude settings:", error);
    return res.json(
      { error: "Failed to check claude settings" },
      { status: 500 }
    );
  }
}

// POST - Backup old fields and write new settings
export async function POST_handler(req, res) {
  try {
    const { env } = req.body;
    
    if (!env || typeof env !== "object") {
      return res.json(
        { error: "Invalid env object" },
        { status: 400 }
      );
    }

    const settingsPath = getClaudeSettingsPath();
    const claudeDir = path.dirname(settingsPath);

    // Ensure .claude directory exists
    await fs.mkdir(claudeDir, { recursive: true });

    // Read current settings
    let currentSettings = {};
    try {
      const content = await fs.readFile(settingsPath, "utf-8");
      currentSettings = JSON.parse(content);
    } catch (error) {
      if (error.code !== "ENOENT") {
        throw error;
      }
    }

    // Normalize ANTHROPIC_BASE_URL to ensure /v1 suffix
    if (env.ANTHROPIC_BASE_URL) {
      env.ANTHROPIC_BASE_URL = env.ANTHROPIC_BASE_URL.endsWith("/v1") 
        ? env.ANTHROPIC_BASE_URL 
        : `${env.ANTHROPIC_BASE_URL}/v1`;
    }

    // Merge new env with existing settings
    const newSettings = {
      ...currentSettings,
      hasCompletedOnboarding: true,
      env: {
        ...(currentSettings.env || {}),
        ...env,
      },
    };

    // Write new settings
    await fs.writeFile(settingsPath, JSON.stringify(newSettings, null, 2));

    return res.json({
      success: true,
      message: "Settings updated successfully",
    });
  } catch (error) {
    console.log("Error updating claude settings:", error);
    return res.json(
      { error: "Failed to update claude settings" },
      { status: 500 }
    );
  }
}

// Fields to remove when resetting
const RESET_ENV_KEYS = [
  "ANTHROPIC_BASE_URL",
  "ANTHROPIC_AUTH_TOKEN",
  "ANTHROPIC_DEFAULT_OPUS_MODEL",
  "ANTHROPIC_DEFAULT_SONNET_MODEL",
  "ANTHROPIC_DEFAULT_HAIKU_MODEL",
  "API_TIMEOUT_MS",
];

// DELETE - Reset settings (remove env fields)
export async function DELETE(req, res) {
  try {
    const settingsPath = getClaudeSettingsPath();

    // Read current settings
    let currentSettings = {};
    try {
      const content = await fs.readFile(settingsPath, "utf-8");
      currentSettings = JSON.parse(content);
    } catch (error) {
      if (error.code === "ENOENT") {
        return res.json({
          success: true,
          message: "No settings file to reset",
        });
      }
      throw error;
    }

    // Remove specified env fields
    if (currentSettings.env) {
      RESET_ENV_KEYS.forEach((key) => {
        delete currentSettings.env[key];
      });
      
      // Clean up empty env object
      if (Object.keys(currentSettings.env).length === 0) {
        delete currentSettings.env;
      }
    }

    // Write updated settings
    await fs.writeFile(settingsPath, JSON.stringify(currentSettings, null, 2));

    return res.json({
      success: true,
      message: "Settings reset successfully",
    });
  } catch (error) {
    console.log("Error resetting claude settings:", error);
    return res.json(
      { error: "Failed to reset claude settings" },
      { status: 500 }
    );
  }
}

