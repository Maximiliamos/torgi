import crypto from "node:crypto";
import fsp from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { chromium } from "playwright";

const PORT = Number(process.env.PORT || 18443);
const TOKEN = String(process.env.TBANKROT_AUTH_BROKER_TOKEN || "");
const COOKIE_FILE = String(process.env.TBANKROT_COOKIE_FILE || "/data/tbankrot-cookies.json");
const VIEWPORT = { width: 1365, height: 768 };
const SESSION_TTL_MS = 30 * 60 * 1000;
const TARGET_ORIGIN = "https://tbankrot.ru";
const VERIFY_URL = "https://tbankrot.ru/?p=search&parent_cat=2&sub_cat=3%2C4%2C5";
const sessions = new Map();

if (TOKEN.length < 32) throw new Error("TBANKROT_AUTH_BROKER_TOKEN must contain at least 32 characters");

function tokenMatches(request) {
  const supplied = String(request.headers.authorization || "").replace(/^Bearer\s+/i, "");
  const expected = Buffer.from(TOKEN);
  const actual = Buffer.from(supplied);
  return actual.length === expected.length && crypto.timingSafeEqual(actual, expected);
}

function json(response, status, value) {
  const body = Buffer.from(JSON.stringify(value));
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": String(body.length),
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
  });
  response.end(body);
}

async function readBody(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > 1024 * 1024) throw new Error("request body too large");
    chunks.push(chunk);
  }
  if (!chunks.length) return {};
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function tbankrotCookies(cookies) {
  return cookies.filter((cookie) =>
    String(cookie.domain || "").replace(/^\./, "").toLowerCase().endsWith("tbankrot.ru")
  );
}

async function loadCookies() {
  try {
    const payload = JSON.parse(await fsp.readFile(COOKIE_FILE, "utf8"));
    const cookies = Array.isArray(payload) ? payload : payload.cookies;
    return Array.isArray(cookies) ? tbankrotCookies(cookies) : [];
  } catch (error) {
    if (error && error.code === "ENOENT") return [];
    throw new Error("saved TBankrot session is invalid");
  }
}

async function savedSessionMetadata() {
  try {
    const payload = JSON.parse(await fsp.readFile(COOKIE_FILE, "utf8"));
    return {
      saved: true,
      captured_at: typeof payload?.capturedAt === "string" ? payload.capturedAt : null,
      version: Number(payload?.version || 1),
    };
  } catch {
    return { saved: false, captured_at: null, version: null };
  }
}

async function saveCookies(context) {
  const cookies = tbankrotCookies(await context.cookies(TARGET_ORIGIN));
  if (!cookies.length) throw new Error("TBankrot did not issue session cookies");
  const directory = path.dirname(COOKIE_FILE);
  await fsp.mkdir(directory, { recursive: true });
  const capturedAt = new Date().toISOString();
  const payload = JSON.stringify({
    version: 2,
    capturedAt,
    source: "sterdez-web-auth",
    cookies,
  });
  const temporary = path.join(directory, ".tbankrot-cookies-" + crypto.randomUUID() + ".tmp");
  await fsp.writeFile(temporary, payload, { encoding: "utf8", mode: 0o600 });
  await fsp.rename(temporary, COOKIE_FILE);
  try { await fsp.chmod(COOKIE_FILE, 0o600); } catch {}
  return { captured_at: capturedAt, cookie_count: cookies.length };
}

async function inspectAccess(context) {
  const page = await context.newPage();
  try {
    await page.goto(VERIFY_URL, { waitUntil: "domcontentloaded", timeout: 30000 });
    const result = await page.evaluate(() => {
      const text = document.body?.innerText || "";
      const blocked = Boolean(document.querySelector(".lot_list_container.blur"));
      const prompt = /Для\s+просмотра\s+лотов/i.test(text);
      const loginForm = Boolean(document.querySelector('input[type="password"]')) && /войти|авторизац/i.test(text);
      const cards = document.querySelectorAll(".lot_container").length;
      const hasSearchEvidence = cards > 0 || /Найдено\s+лотов/i.test(text);
      return { blocked, prompt, loginForm, cards, hasSearchEvidence, title: document.title || "" };
    });
    const ok = !(result.blocked && result.prompt) && !result.loginForm && result.hasSearchEvidence;
    return {
      ok,
      state: ok ? "authenticated" : "auth_required",
      cards: result.cards,
    };
  } finally {
    await page.close().catch(() => undefined);
  }
}

async function verifyContext(context) {
  const result = await inspectAccess(context);
  if (!result.ok) return result;
  const saved = await saveCookies(context);
  return { ...result, ...saved };
}

async function probeSavedSession() {
  const cookies = await loadCookies();
  if (!cookies.length) return { ok: false, state: "auth_required", saved: false };
  const context = await browser.newContext({
    viewport: VIEWPORT,
    locale: "ru-RU",
    timezoneId: "Europe/Moscow",
  });
  try {
    await context.addCookies(cookies);
    const result = await inspectAccess(context);
    return { ...result, saved: true };
  } finally {
    await context.close().catch(() => undefined);
  }
}

function guardTopLevelNavigation(page) {
  page.on("framenavigated", async (frame) => {
    if (frame !== page.mainFrame()) return;
    try {
      const current = new URL(frame.url());
      const host = current.hostname.replace(/^www\./, "").toLowerCase();
      if (host !== "tbankrot.ru" && !host.endsWith(".tbankrot.ru")) {
        await page.goto(TARGET_ORIGIN, { waitUntil: "domcontentloaded", timeout: 30000 });
      }
    } catch {
      // about:blank during browser startup is expected.
    }
  });
}

const browser = await chromium.launch({
  headless: true,
  args: ["--disable-dev-shm-usage", "--no-sandbox"],
});

async function createSession() {
  for (const current of sessions.values()) {
    current.lastUsedAt = Date.now();
    return current;
  }
  const context = await browser.newContext({
    viewport: VIEWPORT,
    locale: "ru-RU",
    timezoneId: "Europe/Moscow",
  });
  const saved = await loadCookies();
  if (saved.length) await context.addCookies(saved);
  const page = await context.newPage();
  guardTopLevelNavigation(page);
  await page.goto(TARGET_ORIGIN, { waitUntil: "domcontentloaded", timeout: 30000 });
  const session = {
    id: crypto.randomUUID(),
    context,
    page,
    createdAt: Date.now(),
    lastUsedAt: Date.now(),
  };
  sessions.set(session.id, session);
  return session;
}

function getSession(id) {
  const session = sessions.get(id);
  if (!session) return null;
  session.lastUsedAt = Date.now();
  return session;
}

async function closeSession(id) {
  const session = sessions.get(id);
  if (!session) return false;
  sessions.delete(id);
  await session.context.close().catch(() => undefined);
  return true;
}

setInterval(() => {
  const cutoff = Date.now() - SESSION_TTL_MS;
  for (const [id, session] of sessions.entries()) {
    if (session.lastUsedAt < cutoff) void closeSession(id);
  }
}, 60000).unref();

async function handleAction(session, body) {
  const type = String(body.type || "");
  if (type === "click") {
    const x = Math.max(0, Math.min(VIEWPORT.width, Number(body.x)));
    const y = Math.max(0, Math.min(VIEWPORT.height, Number(body.y)));
    if (!Number.isFinite(x) || !Number.isFinite(y)) throw new Error("invalid click coordinates");
    await session.page.mouse.click(x, y);
  } else if (type === "text") {
    const value = String(body.value ?? "");
    if (value.length > 4000) throw new Error("text input is too long");
    await session.page.keyboard.insertText(value);
  } else if (type === "key") {
    const allowed = new Set([
      "Backspace", "Tab", "Enter", "Escape", "Delete",
      "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
      "Home", "End", "PageUp", "PageDown",
      "Control+A", "Meta+A",
    ]);
    const key = String(body.key || "");
    if (!allowed.has(key)) throw new Error("unsupported key");
    await session.page.keyboard.press(key);
  } else if (type === "wheel") {
    const deltaY = Math.max(-2000, Math.min(2000, Number(body.delta_y)));
    if (!Number.isFinite(deltaY)) throw new Error("invalid wheel delta");
    await session.page.mouse.wheel(0, deltaY);
  } else if (type === "reload") {
    await session.page.reload({ waitUntil: "domcontentloaded", timeout: 30000 });
  } else if (type === "back") {
    await session.page.goBack({ waitUntil: "domcontentloaded", timeout: 30000 }).catch(() => undefined);
  } else {
    throw new Error("unsupported action");
  }
}

const server = http.createServer(async (request, response) => {
  try {
    if (request.url === "/health" && request.method === "GET") {
      return json(response, 200, { status: "ok" });
    }
    if (!tokenMatches(request)) return json(response, 401, { detail: "unauthorized" });

    const url = new URL(request.url || "/", "http://broker");
    if (url.pathname === "/status" && request.method === "GET") {
      const metadata = await savedSessionMetadata();
      const active = [...sessions.values()][0];
      return json(response, 200, {
        ...metadata,
        browser_ready: browser.isConnected(),
        active_session_id: active?.id || null,
        viewport: VIEWPORT,
      });
    }
    if (url.pathname === "/probe" && request.method === "GET") {
      return json(response, 200, await probeSavedSession());
    }
    if (url.pathname === "/session/start" && request.method === "POST") {
      const session = await createSession();
      return json(response, 200, {
        session_id: session.id,
        viewport: VIEWPORT,
        url: session.page.url(),
        title: await session.page.title(),
      });
    }

    const match = url.pathname.match(/^\/session\/([0-9a-f-]+)\/(frame|action|verify|close)$/i);
    if (!match) return json(response, 404, { detail: "not found" });
    const [, id, operation] = match;
    const session = getSession(id);
    if (!session) return json(response, 404, { detail: "session not found" });

    if (operation === "frame" && request.method === "GET") {
      const image = await session.page.screenshot({ type: "jpeg", quality: 72 });
      response.writeHead(200, {
        "Content-Type": "image/jpeg",
        "Content-Length": String(image.length),
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "X-Content-Type-Options": "nosniff",
      });
      return response.end(image);
    }
    if (operation === "action" && request.method === "POST") {
      await handleAction(session, await readBody(request));
      return json(response, 200, {
        status: "ok",
        url: session.page.url(),
        title: await session.page.title(),
      });
    }
    if (operation === "verify" && request.method === "POST") {
      return json(response, 200, await verifyContext(session.context));
    }
    if (operation === "close" && request.method === "POST") {
      await closeSession(id);
      return json(response, 200, { status: "closed" });
    }
    return json(response, 405, { detail: "method not allowed" });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return json(response, 500, { detail: message.slice(0, 500) });
  }
});

server.listen(PORT, "0.0.0.0", () => {
  process.stdout.write("TBankrot auth broker listening on :" + PORT + "\n");
});

async function shutdown() {
  server.close();
  for (const id of [...sessions.keys()]) await closeSession(id);
  await browser.close().catch(() => undefined);
  process.exit(0);
}

process.on("SIGTERM", () => void shutdown());
process.on("SIGINT", () => void shutdown());
