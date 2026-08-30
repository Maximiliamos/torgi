import fs from "node:fs";
import path from "node:path";
import readline from "node:readline/promises";
import process from "node:process";
import playwright from "../WEB/node_modules/playwright/index.js";

const { chromium } = playwright;

const output = process.argv[2];
if (!output) throw new Error("Output path is required");
const profile = path.join(path.dirname(output), "tbankrot-browser-profile");
fs.mkdirSync(path.dirname(output), { recursive: true });
const context = await chromium.launchPersistentContext(profile, { headless: false });
const page = context.pages()[0] ?? await context.newPage();
await page.goto("https://tbankrot.ru/", { waitUntil: "domcontentloaded" });
const prompt = readline.createInterface({ input: process.stdin, output: process.stdout });
await prompt.question("Sign in to TBankrot and complete CAPTCHA in the opened window. Then press Enter here...");
const cookies = (await context.cookies("https://tbankrot.ru/"))
  .filter((cookie) => cookie.domain.replace(/^\./, "").endsWith("tbankrot.ru"));
if (!cookies.length) throw new Error("TBankrot cookies were not found");
fs.writeFileSync(output, JSON.stringify({ version: 1, capturedAt: new Date().toISOString(), cookies }), { encoding: "utf8", mode: 0o600 });
await context.close();
prompt.close();
process.stdout.write(`Session saved: ${output}\n`);
