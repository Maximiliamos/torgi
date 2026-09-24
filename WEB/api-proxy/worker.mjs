/* global AbortSignal, Headers, Request, Response, URL, console, crypto, fetch, caches, TextEncoder, atob, btoa */

const DEFAULT_PRIMARY_ORIGIN = "https://home-relay.194-226-126-233.sslip.io";
const SAFE_METHODS = new Set(["GET", "HEAD"]);
const TRANSPORT_STATUSES = new Set([502, 504]);
const UPSTREAM_TIMEOUT_MS = 10_000;
const MAP_ATTEMPT_TIMEOUT_MS = 10_000;
const MAP_FALLBACK_TIMEOUT_MS = 8_000;
const PUBLIC_SOURCE_TIMEOUT_MS = 4_000;
const TORGI_PROXY_PREFIX = "/__public-source/torgi";
const TORGI_ALLOWED_PATHS = ["/new/api/public/", "/new/public/"];

const YANDEX_MAP_TILE_RE = /^\/api\/map\/yandex-tiles\/([^/]+)\/(\d+)\/(\d+)\/(\d+)$/;
const CURRENT_MAP_DATASET_PATH = "/api/map/datasets/current";
const MAP_EDGE_COOKIE = "bankrotai_map_edge";
const MAP_EDGE_TOKEN_VERSION = "v1";
const MAP_EDGE_TOKEN_TTL_SECONDS = 300;
const MAP_TILE_BROWSER_CACHE = "private, max-age=31536000, immutable";
const MAP_TILE_EDGE_CACHE = "public, max-age=31536000, immutable";
const MAP_DATASET_BROWSER_CACHE = "private, max-age=5, stale-while-revalidate=30";
const MAP_DATASET_EDGE_CACHE = "public, max-age=5";
let mapEdgeKeySource = null;
let mapEdgeKeyPromise = null;

async function publicSourceResponse(request, incoming) {
  if (!SAFE_METHODS.has(request.method)) return new Response("Method Not Allowed", { status: 405 });
  const path = incoming.pathname.slice(TORGI_PROXY_PREFIX.length);
  if (!TORGI_ALLOWED_PATHS.some((prefix) => path.startsWith(prefix)) || incoming.search.length > 4096) {
    return new Response("Not Found", { status: 404 });
  }
  const target = new URL(`${path}${incoming.search}`, "https://torgi.gov.ru");
  try {
    const response = await fetch(new Request(target, {
      method: request.method,
      headers: { accept: request.headers.get("accept") || "application/json" },
      signal: AbortSignal.timeout(PUBLIC_SOURCE_TIMEOUT_MS),
    }));
    const headers = new Headers(response.headers);
    headers.delete("set-cookie");
    headers.set("cache-control", "no-store");
    return new Response(request.method === "HEAD" ? null : await response.arrayBuffer(), {
      status: response.status,
      headers,
    });
  } catch {
    return Response.json({ detail: "Public source is temporarily unavailable" }, {
      status: 502,
      headers: { "cache-control": "no-store" },
    });
  }
}

function normalizedOrigin(value, fallback = null) {
  value = value?.trim();
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" ? url.origin : null;
  } catch {
    return fallback;
  }
}

function primaryOrigin(env) {
  return normalizedOrigin(env.PRIMARY_API_ORIGIN, DEFAULT_PRIMARY_ORIGIN)
    || DEFAULT_PRIMARY_ORIGIN;
}

function secondaryOrigin(env) {
  return normalizedOrigin(env.SECONDARY_API_ORIGIN);
}

function upstreamRequest(request, incoming, origin, headers, timeoutMs = UPSTREAM_TIMEOUT_MS) {
  const upstream = new URL(`${incoming.pathname}${incoming.search}`, origin);
  return new Request(upstream, {
    method: request.method,
    headers,
    body: SAFE_METHODS.has(request.method) ? undefined : request.body,
    duplex: SAFE_METHODS.has(request.method) ? undefined : "half",
    redirect: "manual",
    signal: AbortSignal.timeout(timeoutMs),
  });
}

async function completedResponse(request, incoming, origin, headers, timeoutMs = UPSTREAM_TIMEOUT_MS, notFoundIsFailure = false) {
  const response = await fetch(upstreamRequest(request, incoming, origin, headers, timeoutMs));
  if (TRANSPORT_STATUSES.has(response.status) || (notFoundIsFailure && response.status === 404)) {
    throw new Error(`Upstream transport status ${response.status}`);
  }
  // Buffer safe reads before returning headers. This catches the production
  // failure where the origin sends HTTP 200 headers but never completes body.
  const bodyForbidden = request.method === "HEAD" || [204, 205, 304].includes(response.status);
  const body = bodyForbidden ? null : await response.arrayBuffer();
  return { response, body };
}


function yandexTileParts(pathname) {
  const match = YANDEX_MAP_TILE_RE.exec(pathname);
  if (!match) return null;
  return {
    version: match[1],
    z: Number(match[2]),
    x: Number(match[3]),
    y: Number(match[4]),
  };
}

function mapTileR2Key(parts) {
  return `yandex-tiles/${parts.version}/${parts.z}/${parts.x}/${parts.y}.json`;
}

function mapCache(env) {
  return env.MAP_EDGE_CACHE || globalThis.caches?.default || null;
}

function schedule(ctx, promise) {
  if (!promise || typeof promise.then !== "function") return;
  if (ctx?.waitUntil) {
    ctx.waitUntil(promise);
  } else {
    promise.catch(() => undefined);
  }
}

function base64UrlEncode(bytes) {
  let binary = "";
  for (const byte of new Uint8Array(bytes)) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function base64UrlDecode(value) {
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized + "=".repeat((4 - normalized.length % 4) % 4);
  const binary = atob(padded);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

async function mapEdgeKey(secret) {
  if (!secret) return null;
  if (mapEdgeKeySource !== secret || !mapEdgeKeyPromise) {
    mapEdgeKeySource = secret;
    mapEdgeKeyPromise = (async () => {
      const material = new TextEncoder().encode(`sterdez-map-edge-v1:${secret}`);
      const digest = await crypto.subtle.digest("SHA-256", material);
      return crypto.subtle.importKey(
        "raw",
        digest,
        { name: "HMAC", hash: "SHA-256" },
        false,
        ["sign", "verify"],
      );
    })();
  }
  return mapEdgeKeyPromise;
}

async function createMapEdgeToken(secret, nowSeconds = Math.floor(Date.now() / 1000)) {
  const key = await mapEdgeKey(secret);
  if (!key) return null;
  const expiresAt = nowSeconds + MAP_EDGE_TOKEN_TTL_SECONDS;
  const nonce = crypto.randomUUID().replace(/-/g, "");
  const message = `${MAP_EDGE_TOKEN_VERSION}.${expiresAt}.${nonce}`;
  const signature = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(message),
  );
  return `${message}.${base64UrlEncode(signature)}`;
}

async function verifyMapEdgeToken(token, secret, nowSeconds = Math.floor(Date.now() / 1000)) {
  if (!token || !secret) return false;
  const parts = token.split(".");
  if (parts.length !== 4 || parts[0] !== MAP_EDGE_TOKEN_VERSION) return false;
  const expiresAt = Number(parts[1]);
  if (!Number.isInteger(expiresAt)) return false;
  if (expiresAt < nowSeconds || expiresAt > nowSeconds + MAP_EDGE_TOKEN_TTL_SECONDS + 30) return false;
  try {
    const key = await mapEdgeKey(secret);
    if (!key) return false;
    const message = parts.slice(0, 3).join(".");
    return crypto.subtle.verify(
      "HMAC",
      key,
      base64UrlDecode(parts[3]),
      new TextEncoder().encode(message),
    );
  } catch {
    return false;
  }
}

function cookieValue(request, name) {
  const raw = request.headers.get("cookie") || "";
  for (const segment of raw.split(";")) {
    const separator = segment.indexOf("=");
    if (separator < 0) continue;
    const key = segment.slice(0, separator).trim();
    if (key === name) return segment.slice(separator + 1).trim();
  }
  return "";
}

function mapEdgeCookie(token) {
  return [
    `${MAP_EDGE_COOKIE}=${token}`,
    "Max-Age=" + MAP_EDGE_TOKEN_TTL_SECONDS,
    "Path=/api/map/",
    "HttpOnly",
    "Secure",
    "SameSite=Strict",
  ].join("; ");
}

async function hasMapEdgeSession(request, env) {
  return verifyMapEdgeToken(
    cookieValue(request, MAP_EDGE_COOKIE),
    env.KOYEB_SERVICE_KEY,
  );
}

function cacheKeyFor(request, incoming) {
  return new Request(incoming.toString(), {
    method: "GET",
    headers: { accept: request.headers.get("accept") || "application/json" },
  });
}

function privateMapHeaders(source, cacheControl, cacheState, requestId) {
  const headers = new Headers(source);
  headers.delete("set-cookie");
  headers.set("cache-control", cacheControl);
  headers.set("x-content-type-options", "nosniff");
  headers.set("referrer-policy", "same-origin");
  headers.set("x-map-cache", cacheState);
  headers.set("x-request-id", requestId);
  return headers;
}

function internalMapCacheHeaders(source, cacheControl) {
  const headers = new Headers(source);
  headers.delete("set-cookie");
  headers.set("cache-control", cacheControl);
  headers.set("content-type", headers.get("content-type") || "application/json; charset=utf-8");
  return headers;
}

function conditionalMapResponse(request, response, headers) {
  const etag = headers.get("etag");
  if (etag && request.headers.get("if-none-match") === etag) {
    return new Response(null, { status: 304, headers });
  }
  if (request.method === "HEAD") {
    return new Response(null, { status: response.status, statusText: response.statusText, headers });
  }
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

function originHeadersFor(request, incoming, env, requestId, { stripConditionals = false } = {}) {
  const headers = new Headers(request.headers);
  headers.delete("authorization");
  headers.delete("x-api-key");
  headers.delete("host");
  if (stripConditionals) {
    headers.delete("if-none-match");
    headers.delete("if-modified-since");
  }
  headers.set("x-api-key", env.KOYEB_SERVICE_KEY);
  headers.set("x-forwarded-host", incoming.host);
  headers.set("x-forwarded-proto", "https");
  headers.set("x-request-id", requestId);
  return headers;
}

async function currentDatasetResponse(request, incoming, env, ctx, requestId) {
  if (!SAFE_METHODS.has(request.method)) {
    return new Response("Method Not Allowed", { status: 405, headers: { "cache-control": "no-store" } });
  }

  const cache = mapCache(env);
  const cacheKey = cacheKeyFor(request, incoming);
  const edgeAuthorized = await hasMapEdgeSession(request, env);

  if (edgeAuthorized && cache) {
    const hit = await cache.match(cacheKey);
    if (hit) {
      const headers = privateMapHeaders(
        hit.headers,
        MAP_DATASET_BROWSER_CACHE,
        "EDGE-HIT",
        requestId,
      );
      return conditionalMapResponse(request, hit, headers);
    }
  }

  const headers = originHeadersFor(request, incoming, env, requestId);
  const origin = await completedResponse(
    request,
    incoming,
    primaryOrigin(env),
    headers,
    UPSTREAM_TIMEOUT_MS,
  );
  const outgoing = privateMapHeaders(
    origin.response.headers,
    MAP_DATASET_BROWSER_CACHE,
    "ORIGIN-AUTH",
    requestId,
  );

  if ([200, 304].includes(origin.response.status)) {
    const token = await createMapEdgeToken(env.KOYEB_SERVICE_KEY);
    if (token) outgoing.append("set-cookie", mapEdgeCookie(token));
  }

  if (request.method === "GET" && origin.response.status === 200 && origin.body && cache) {
    const internal = new Response(origin.body.slice(0), {
      status: 200,
      headers: internalMapCacheHeaders(origin.response.headers, MAP_DATASET_EDGE_CACHE),
    });
    schedule(ctx, cache.put(cacheKey, internal));
  }

  return new Response(request.method === "HEAD" ? null : origin.body, {
    status: origin.response.status,
    statusText: origin.response.statusText,
    headers: outgoing,
  });
}

async function yandexMapTileResponse(request, incoming, env, ctx, requestId, parts) {
  if (!SAFE_METHODS.has(request.method)) {
    return new Response("Method Not Allowed", { status: 405, headers: { "cache-control": "no-store" } });
  }

  if (!(await hasMapEdgeSession(request, env))) {
    return Response.json(
      { detail: "Map edge session required" },
      {
        status: 401,
        headers: {
          "cache-control": "no-store",
          "x-map-auth": "refresh",
          "x-request-id": requestId,
        },
      },
    );
  }

  const cache = mapCache(env);
  const cacheKey = cacheKeyFor(request, incoming);

  if (cache) {
    const hit = await cache.match(cacheKey);
    if (hit) {
      const headers = privateMapHeaders(hit.headers, MAP_TILE_BROWSER_CACHE, "EDGE-HIT", requestId);
      return conditionalMapResponse(request, hit, headers);
    }
  }

  const r2Key = mapTileR2Key(parts);
  if (env.MAP_ASSETS) {
    const object = await env.MAP_ASSETS.get(r2Key);
    if (object) {
      const internalHeaders = new Headers();
      object.writeHttpMetadata?.(internalHeaders);
      internalHeaders.set("content-type", internalHeaders.get("content-type") || "application/json; charset=utf-8");
      internalHeaders.set(
        "etag",
        object.customMetadata?.originEtag || object.httpEtag || `"${object.etag}"`,
      );
      internalHeaders.set("x-map-dataset", parts.version);
      internalHeaders.set("cache-control", MAP_TILE_EDGE_CACHE);
      const internal = new Response(object.body, { status: 200, headers: internalHeaders });
      if (cache && request.method === "GET") {
        schedule(ctx, cache.put(cacheKey, internal.clone()));
      }
      const headers = privateMapHeaders(internal.headers, MAP_TILE_BROWSER_CACHE, "R2-HIT", requestId);
      return conditionalMapResponse(request, internal, headers);
    }
  }

  const originHeaders = originHeadersFor(
    request,
    incoming,
    env,
    requestId,
    { stripConditionals: true },
  );
  const origin = await completedResponse(
    request,
    incoming,
    primaryOrigin(env),
    originHeaders,
    MAP_ATTEMPT_TIMEOUT_MS,
  );

  const outgoing = privateMapHeaders(
    origin.response.headers,
    origin.response.ok ? MAP_TILE_BROWSER_CACHE : "no-store",
    "ORIGIN-MISS",
    requestId,
  );

  if (request.method === "GET" && origin.response.ok && origin.body) {
    const bodyForCache = origin.body.slice(0);
    const internalHeaders = internalMapCacheHeaders(origin.response.headers, MAP_TILE_EDGE_CACHE);
    const internal = new Response(bodyForCache, { status: origin.response.status, headers: internalHeaders });
    if (cache) schedule(ctx, cache.put(cacheKey, internal));

    if (env.MAP_ASSETS) {
      const metadata = {
        httpMetadata: {
          contentType: origin.response.headers.get("content-type") || "application/json; charset=utf-8",
          cacheControl: MAP_TILE_EDGE_CACHE,
        },
        customMetadata: {
          dataset: parts.version,
          z: String(parts.z),
          x: String(parts.x),
          y: String(parts.y),
        },
      };
      const originEtag = origin.response.headers.get("etag");
      if (originEtag) metadata.customMetadata.originEtag = originEtag;
      schedule(ctx, env.MAP_ASSETS.put(r2Key, origin.body.slice(0), metadata));
    }
  }

  const response = new Response(request.method === "HEAD" ? null : origin.body, {
    status: origin.response.status,
    statusText: origin.response.statusText,
    headers: outgoing,
  });
  return conditionalMapResponse(request, response, outgoing);
}

function browserPrivateCachePolicy(request, incoming, response) {
  if (!SAFE_METHODS.has(request.method) || ![200, 304].includes(response.status)) return null;
  if (/^\/api\/map\/tiles\/[^/]+\/\d+\/\d+\/\d+$/.test(incoming.pathname)) {
    return "private, max-age=86400, immutable";
  }
  if (incoming.pathname === "/api/map/datasets/current") {
    return MAP_DATASET_BROWSER_CACHE;
  }
  if (incoming.pathname === "/api/map/lots") {
    return response.headers.get("cache-control") || "private, max-age=60, stale-while-revalidate=300";
  }
  return null;
}

function proxyResponse(result, requestId, request, incoming) {
  const outgoing = new Headers(result.response.headers);
  outgoing.set(
    "cache-control",
    browserPrivateCachePolicy(request, incoming, result.response) || "no-store",
  );
  outgoing.set("x-content-type-options", "nosniff");
  outgoing.set("referrer-policy", "same-origin");
  outgoing.set("x-request-id", result.response.headers.get("x-request-id") || requestId);
  return new Response(result.body, {
    status: result.response.status,
    statusText: result.response.statusText,
    headers: outgoing,
  });
}

export default {
  async fetch(request, env, ctx) {
    const suppliedRequestId = request.headers.get("x-request-id")?.trim();
    const requestId = suppliedRequestId && suppliedRequestId.length <= 128
      ? suppliedRequestId
      : crypto.randomUUID();
    const startedAt = Date.now();
    const incoming = new URL(request.url);
    if (incoming.pathname.startsWith(TORGI_PROXY_PREFIX)) {
      return publicSourceResponse(request, incoming);
    }

    if (incoming.pathname === CURRENT_MAP_DATASET_PATH) {
      try {
        return await currentDatasetResponse(request, incoming, env, ctx, requestId);
      } catch (error) {
        console.error(JSON.stringify({
          event: "map_dataset_edge_failure",
          request_id: requestId,
          path: incoming.pathname,
          error: error instanceof Error ? error.name : "UnknownError",
        }));
        return Response.json(
          { detail: "Map dataset temporarily unavailable" },
          { status: 502, headers: { "cache-control": "no-store", "x-request-id": requestId } },
        );
      }
    }

    const tileParts = yandexTileParts(incoming.pathname);
    if (tileParts) {
      try {
        return await yandexMapTileResponse(request, incoming, env, ctx, requestId, tileParts);
      } catch (error) {
        console.error(JSON.stringify({
          event: "map_tile_edge_failure",
          request_id: requestId,
          path: incoming.pathname,
          error: error instanceof Error ? error.name : "UnknownError",
        }));
        return Response.json(
          { detail: "Map tile temporarily unavailable" },
          { status: 502, headers: { "cache-control": "no-store", "x-request-id": requestId } },
        );
      }
    }

    const headers = new Headers(request.headers);
    headers.delete("authorization");
    headers.delete("x-api-key");
    headers.delete("host");
    headers.set("x-api-key", env.KOYEB_SERVICE_KEY);
    headers.set("x-forwarded-host", incoming.host);
    headers.set("x-forwarded-proto", "https");
    headers.set("x-request-id", requestId);
    const mapRead = request.method === "GET" && incoming.pathname === "/api/map/lots";
    const retrySafeRead = mapRead
      || (SAFE_METHODS.has(request.method) && incoming.pathname.startsWith("/health/"));

    try {
      const primary = await completedResponse(
        request, incoming, primaryOrigin(env), headers,
        mapRead ? MAP_ATTEMPT_TIMEOUT_MS : UPSTREAM_TIMEOUT_MS,
        SAFE_METHODS.has(request.method)
          && incoming.pathname === "/api/time"
          && Boolean(secondaryOrigin(env)),
      );
      console.log(JSON.stringify({
        event: "primary_success", request_id: requestId, method: request.method,
        path: incoming.pathname, status: primary.response.status, duration_ms: Date.now() - startedAt,
      }));
      return proxyResponse(primary, requestId, request, incoming);
    } catch (primaryError) {
      console.error(JSON.stringify({
        event: "primary_failure", request_id: requestId, method: request.method,
        path: incoming.pathname, duration_ms: Date.now() - startedAt,
        error: primaryError instanceof Error ? primaryError.name : "UnknownError",
      }));
      if (retrySafeRead) {
        try {
          const retry = await completedResponse(
            request, incoming, primaryOrigin(env), headers,
            mapRead ? MAP_ATTEMPT_TIMEOUT_MS : UPSTREAM_TIMEOUT_MS,
            incoming.pathname === "/api/time" && Boolean(secondaryOrigin(env)),
          );
          console.log(JSON.stringify({
            event: "primary_retry_success", request_id: requestId, method: request.method,
            path: incoming.pathname, status: retry.response.status, duration_ms: Date.now() - startedAt,
          }));
          return proxyResponse(retry, requestId, request, incoming);
        } catch (retryError) {
          console.error(JSON.stringify({
            event: "primary_retry_failure", request_id: requestId, method: request.method,
            path: incoming.pathname, duration_ms: Date.now() - startedAt,
            error: retryError instanceof Error ? retryError.name : "UnknownError",
          }));
        }
      }
      const fallbackOrigin = SAFE_METHODS.has(request.method) ? secondaryOrigin(env) : null;
      if (fallbackOrigin) {
        console.log(JSON.stringify({
          event: "fallback_activation", request_id: requestId, method: request.method, path: incoming.pathname,
        }));
        try {
          const secondary = await completedResponse(
            request, incoming, fallbackOrigin, headers,
            mapRead ? MAP_FALLBACK_TIMEOUT_MS : UPSTREAM_TIMEOUT_MS,
          );
          console.log(JSON.stringify({
            event: "secondary_success", request_id: requestId, method: request.method,
            path: incoming.pathname, status: secondary.response.status, duration_ms: Date.now() - startedAt,
          }));
          return proxyResponse(secondary, requestId, request, incoming);
        } catch (secondaryError) {
          console.error(JSON.stringify({
            event: "secondary_failure", request_id: requestId, method: request.method,
            path: incoming.pathname, duration_ms: Date.now() - startedAt,
            error: secondaryError instanceof Error ? secondaryError.name : "UnknownError",
          }));
        }
      }
      return Response.json({ detail: "API upstream is temporarily unavailable" }, {
        status: 502,
        headers: { "cache-control": "no-store", "x-request-id": requestId },
      });
    }
  },
};
