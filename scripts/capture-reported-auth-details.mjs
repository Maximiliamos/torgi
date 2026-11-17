// User-run, read-only HTML capture. Credentials/cookies/storage state are never exported.
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import readline from "node:readline/promises";
import { pathToFileURL } from "node:url";
import { createHash } from "node:crypto";

const [snapshot, destination, playwrightEntry] = process.argv.slice(2);
if (!snapshot || !destination || !playwrightEntry) {
  throw new Error("Usage: node capture-reported-auth-details.mjs snapshot.json output-directory playwright/index.js");
}
const playwrightModule = await import(pathToFileURL(path.resolve(playwrightEntry)).href);
const { chromium } = playwrightModule.default ?? playwrightModule;
const reports = JSON.parse(fs.readFileSync(snapshot, "utf8").replace(/^\uFEFF/, "")).items;
const selected = new Map();
for (const item of reports) {
  if (item.report_id > 44 || !["tbankrot.ru", "sistematorg.com"].includes(item.source_system)) continue;
  if (item.source_system === "tbankrot.ru" && /^tbankrot:\d+$/.test(item.external_id)) {
    selected.set(item.lot_id, { lot_id: item.lot_id, source_system: item.source_system,
      external_id: item.external_id, native_id: item.external_id.split(":")[1],
      url: `https://tbankrot.ru/item?id=${item.external_id.split(":")[1]}` });
  } else if (item.source_system === "sistematorg.com" && /^sistematorg\.com:\d+(?::\d+)?$/.test(item.external_id)) {
    selected.set(item.lot_id, { lot_id: item.lot_id, source_system: item.source_system,
      external_id: item.external_id, native_id: item.external_id.split(":")[1],
      url: `https://sistematorg.com/trade_view.php?trade_nid=${item.external_id.split(":")[1]}` });
  }
}
if (!selected.size || selected.size > 50) throw new Error("Unsafe report identity selection");
fs.mkdirSync(destination, { recursive: true });
const prompt = readline.createInterface({ input: process.stdin, output: process.stdout });
let executablePath = chromium.executablePath();
if (!fs.existsSync(executablePath)) {
  const browserRoot = path.join(process.env.LOCALAPPDATA || "", "ms-playwright");
  const revisions = fs.readdirSync(browserRoot).filter(name => /^chromium-\d+$/.test(name))
    .sort((a, b) => Number(b.split("-")[1]) - Number(a.split("-")[1]));
  executablePath = revisions.flatMap(name => ["chrome-win64", "chrome-win"].map(folder =>
    path.join(browserRoot, name, folder, "chrome.exe"))).find(candidate => fs.existsSync(candidate));
  if (!executablePath) throw new Error("Installed Chromium was not found; no browser was downloaded or installed");
}
const browser = await chromium.launch({ headless: false, executablePath });
const context = await browser.newContext();
const page = await context.newPage();
const results = [];
try {
  await page.goto("https://tbankrot.ru/item?id=7896849", { waitUntil: "domcontentloaded", timeout: 45000 });
  console.log("\nОткрыто отдельное окно браузера. Войдите в TBankrot вручную.");
  console.log("Убедитесь, что полная карточка доступна. Cookies и пароль не сохраняются.");
  await prompt.question("После входа нажмите Enter В ЭТОЙ КОНСОЛИ, чтобы собрать только нужные карточки... ");
  for (const item of selected.values()) {
    const result = { ...item, observed_at: new Date().toISOString() };
    try {
      const response = await page.goto(item.url, { waitUntil: "domcontentloaded", timeout: 45000 });
      result.http_status = response?.status() ?? null;
      result.response_url = page.url();
      if ([401, 403, 429].includes(result.http_status)) {
        throw new Error(`Источник ограничил доступ: HTTP ${result.http_status}; повторные запросы не выполняются`);
      }
      if (result.http_status !== 200) throw new Error(`HTTP ${result.http_status}`);
      if (item.source_system === "tbankrot.ru") {
        const visible = await page.locator("#lot_info").count();
        if (visible !== 1) throw new Error("Точная карточка #lot_info не найдена");
        const observed = await page.locator("#lot_info").getAttribute("data-id")
          || await page.locator("#lot_info").getAttribute("data-rel");
        if (observed !== item.native_id) throw new Error("ID карточки отличается от запрошенного");
        result.auth_limited = await page.locator("#free_activation_field").count() > 0;
      }
      const html = await page.content();
      const name = `${item.source_system.replace(/[^a-z0-9.-]/gi, "_")}_${item.native_id}_browser.html`;
      fs.writeFileSync(path.join(destination, name), html, "utf8");
      result.html_file = name;
      result.html_bytes = Buffer.byteLength(html);
      result.sha256 = createHash("sha256").update(html).digest("hex");
      console.log(`${item.external_id}: HTML сохранён${result.auth_limited ? "; ВНИМАНИЕ: полная карточка всё ещё закрыта" : ""}`);
    } catch (error) {
      result.error = String(error.message || error);
      console.log(`${item.external_id}: ${result.error}`);
    }
    results.push(result);
    fs.writeFileSync(path.join(destination, "summary.json"), JSON.stringify({ database_writes: 0,
      cookie_export: false, items: results }, null, 2), "utf8");
    if ([401, 403, 429].includes(result.http_status)) break;
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
} finally {
  await context.close();
  await browser.close();
  prompt.close();
}
console.log(`\nСбор завершён: ${destination}`);
console.log("Напишите в чате: сбор готов. Архив или cookies отправлять не нужно.");
