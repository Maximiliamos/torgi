// Branch-only diagnostics: no login, credentials, production writes or traces.
import { chromium, request } from '@playwright/test';
import { writeFileSync } from 'node:fs';
const report = { observed_at_utc: new Date().toISOString(), production_writes: 0, variants: [] };
const safePath = (raw) => { const u = new URL(raw); return u.origin + u.pathname; };
const save = () => writeFileSync('public-network-diagnostic.json', JSON.stringify(report, null, 2));
const http = await request.newContext();
for (const url of ['https://sterdez.online/', 'https://api.sterdez.online/health/ready']) {
  const started = Date.now();
  let item;
  try {
    const response = await http.get(url, { timeout: 20000 });
    item = { method: 'node_http', url, status: response.status(), ms: Date.now() - started };
  } catch (error) {
    item = { method: 'node_http', url, ms: Date.now() - started, error: String(error).slice(0, 300) };
  }
  report.variants.push(item); console.log(JSON.stringify(item)); save();
}
await http.dispose();
for (const [name, args] of [
  ['bundled_chromium_default', []],
  ['bundled_chromium_http1', ['--disable-http2', '--disable-quic']],
]) {
  const item = { name, args, events: [] };
  const pending = new Map();
  const started = Date.now();
  const browser = await chromium.launch({ headless: true, args });
  try {
    const page = await browser.newPage();
    page.on('request', (req) => {
      if (['document', 'script', 'stylesheet'].includes(req.resourceType())) {
        const path = safePath(req.url()); pending.set(path, Date.now());
        item.events.push({ type: 'request', url: path, resource: req.resourceType() });
      }
    });
    page.on('response', (res) => {
      const path = safePath(res.url());
      if (pending.has(path)) {
        item.events.push({ type: 'response', url: path, status: res.status(), ms: Date.now() - pending.get(path) });
        pending.delete(path);
      }
    });
    page.on('requestfailed', (req) => {
      const path = safePath(req.url());
      item.events.push({ type: 'failed', url: path, error: req.failure()?.errorText }); pending.delete(path);
    });
    await page.goto('https://sterdez.online/', { waitUntil: 'domcontentloaded', timeout: 40000 });
    item.domcontentloaded_ms = Date.now() - started;
    item.login_visible = await page.locator('input[autocomplete="username"]').waitFor({ state: 'visible', timeout: 5000 }).then(() => true, () => false);
    item.title = await page.title();
    item.body = (await page.locator('body').innerText()).slice(0, 200);
  } catch (error) { item.error = String(error).slice(0, 350); }
  finally {
    item.ms = Date.now() - started; item.pending = [...pending.keys()];
    await browser.close(); report.variants.push(item); save(); console.log(JSON.stringify(item));
  }
}
