/* global AbortSignal, Headers, Request, Response, URL, console, crypto, fetch */

const DEFAULT_PRIMARY_ORIGIN = "https://home-relay.194-226-126-233.sslip.io";
const SAFE_METHODS = new Set(["GET", "HEAD"]);
const TRANSPORT_STATUSES = new Set([502, 504]);
const UPSTREAM_TIMEOUT_MS = 10_000;
const MAP_ATTEMPT_TIMEOUT_MS = 10_000;
const MAP_FALLBACK_TIMEOUT_MS = 8_000;
const PUBLIC_SOURCE_TIMEOUT_MS = 4_000;
const TORGI_PROXY_PREFIX = "/__public-source/torgi";
const TORGI_ALLOWED_PATHS = ["/new/api/public/", "/new/public/"];
const MAP_YANDEX_TILE_RE = /^\/api\/map\/yandex-tiles\/([^/]+)\/(\d+)\/(\d+)\/(\d+)$/;

function parseYandexTilePath(pathname) {
  const match = MAP_YANDEX_TILE_RE.exec(pathname);
  if (!match) return null;
  return {
    version: match[1],
    z: Number(match[2]),
    x: Number(match[3]),
    y: Number(match[4]),
  };
}

function base64UrlBytes(value) {
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized + "=".repeat((4 - normalized.length % 4) % 4);
  const binary = atob(padded);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

function equalBytes(left, right) {
  if (left.length !== right.length) return false;
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) {
    difference |= left[index] ^ right[index];
  }
  return difference === 0;
}

async function verifyMapToken(token, secret, expectedVersion) {
  if (!token || !secret) return false;
  try {
    const [encoded, signature, extra] = token.split(".");
    if (!encoded || !signature || extra !== undefined) return false;
    const key = await crypto.subtle.importKey(
      "raw",
      new TextEncoder().encode(secret),
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["sign"],
    );
    const expected = new Uint8Array(await crypto.subtle.sign(
      "HMAC",
      key,
      new TextEncoder().encode(encoded),
    ));
    if (!equalBytes(base64UrlBytes(signature), expected)) return false;
    const payload = JSON.parse(new TextDecoder().decode(base64UrlBytes(encoded)));
    const now = Math.floor(Date.now() / 1000);
    return Number.isInteger(payload.sub)
      && typeof payload.v === "string"
      && payload.v === expectedVersion
      && Number.isInteger(payload.exp)
      && payload.exp >= now;
  } catch {
    return false;
  }
}

function mapTileR2Key(parts) {
  return `tiles/${parts.version}/${parts.z}/${parts.x}/${parts.y}.json`;
}

function internalMapCacheHeaders(source = new Headers()) {
  const headers = new Headers(source);
  headers.set("cache-control", "public, max-age=31536000, immutable");
  headers.set("content-type", "application/json; charset=utf-8");
  headers.delete("set-cookie");
  return headers;
}

function clientMapHeaders(source = new Headers(), requestId, cacheState) {
  const headers = new Headers(source);
  headers.set("cache-control", "private, max-age=86400, immutable");
  headers.set("x-map-cache", cacheState);
  headers.set("x-request-id", requestId);
  headers.set("x-content-type-options", "nosniff");
  headers.set("referrer-policy", "same-origin");
  headers.delete("set-cookie");
  return headers;
}

async function cachedYandexTileResponse(request, incoming, env, ctx, requestId) {
  const parts = parseYandexTilePath(incoming.pathname);
  if (!parts || !SAFE_METHODS.has(request.method)) return null;

  const token = request.headers.get("x-map-token");
  if (!token || !env.MAP_EDGE_TOKEN_SECRET) return null;
  if (!(await verifyMapToken(token, env.MAP_EDGE_TOKEN_SECRET, parts.version))) {
    return Response.json({ detail: "Map token is invalid or expired" }, {
      status: 401,
      headers: { "cache-control": "no-store", "x-request-id": requestId },
    });
  }

  const edgeCache = globalThis.caches?.default;
  const cacheKey = new Request(incoming.toString(), {
    method: "GET",
    headers: { accept: "application/json" },
  });

  if (edgeCache) {
    const hit = await edgeCache.match(cacheKey);
    if (hit) {
      const body = request.method === "HEAD" ? null : await hit.arrayBuffer();
      return new Response(body, {
        status: hit.status,
        headers: clientMapHeaders(hit.headers, requestId, "EDGE-HIT"),
      });
    }
  }

  const r2Key = mapTileR2Key(parts);
  if (env.MAP_ASSETS) {
    const object = await env.MAP_ASSETS.get(r2Key);
    if (object) {
      const body = await object.arrayBuffer();
      const internalHeaders = internalMapCacheHeaders();
      object.writeHttpMetadata?.(internalHeaders);
      if (object.httpEtag) internalHeaders.set("etag", object.httpEtag);
      const internal = new Response(body.slice(0), {
        status: 200,
        headers: internalHeaders,
      });
      if (edgeCache && request.method === "GET") {
        ctx?.waitUntil?.(edgeCache.put(cacheKey, internal.clone()));
      }
      return new Response(request.method === "HEAD" ? null : body, {
        status: 200,
        headers: clientMapHeaders(internalHeaders, requestId, "R2-HIT"),
      });
    }
  }

  const headers = new Headers(request.headers);
  headers.delete("authorization");
  headers.delete("x-api-key");
  headers.delete("x-map-token");
  headers.delete("host");
  headers.set("x-api-key", env.KOYEB_SERVICE_KEY);
  headers.set("x-forwarded-host", incoming.host);
  headers.set("x-forwarded-proto", "https");
  headers.set("x-request-id", requestId);

  const origin = await completedResponse(
    request,
    incoming,
    primaryOrigin(env),
    headers,
    MAP_ATTEMPT_TIMEOUT_MS,
  );

  if (!origin.response.ok || origin.body == null) {
    return proxyResponse(origin, requestId, request, incoming);
  }

  const internalHeaders = internalMapCacheHeaders(origin.response.headers);
  const body = origin.body;
  const internal = new Response(body.slice(0), {
    status: origin.response.status,
    headers: internalHeaders,
  });

  const writes = [];
  if (env.MAP_ASSETS && request.method === "GET") {
    writes.push(env.MAP_ASSETS.put(r2Key, body.slice(0), {
      httpMetadata: {
        contentType: "application/json; charset=utf-8",
        cacheControl: "public, max-age=31536000, immutable",
      },
      customMetadata: {
        dataset: parts.version,
        z: String(parts.z),
        x: String(parts.x),
        y: String(parts.y),
      },
    }));
  }
  if (edgeCache && request.method === "GET") {
    writes.push(edgeCache.put(cacheKey, internal.clone()));
  }
  if (writes.length) ctx?.waitUntil?.(Promise.all(writes));

  return new Response(request.method === "HEAD" ? null : body, {
    status: origin.response.status,
    headers: clientMapHeaders(internalHeaders, requestId, "ORIGIN-MISS"),
  });
}


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

function browserPrivateCachePolicy(request, incoming, response) {
  if (!SAFE_METHODS.has(request.method) || ![200, 304].includes(response.status)) return null;
  if (/^\/api\/map\/tiles\/[^/]+\/\d+\/\d+\/\d+$/.test(incoming.pathname)) {
    return "private, max-age=86400, immutable";
  }
  if (incoming.pathname === "/api/map/datasets/current") {
    return "private, max-age=15, stale-while-revalidate=60";
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
    const cachedMap = await cachedYandexTileResponse(request, incoming, env, ctx, requestId);
    if (cachedMap) return cachedMap;
    if (incoming.pathname.startsWith(TORGI_PROXY_PREFIX)) {
      return publicSourceResponse(request, incoming);
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
