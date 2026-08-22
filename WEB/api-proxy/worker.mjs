/* global AbortSignal, Headers, Request, Response, URL, console, crypto, fetch */

const DEFAULT_PRIMARY_ORIGIN = "https://194-226-126-233.sslip.io";
const SAFE_METHODS = new Set(["GET", "HEAD"]);
const TRANSPORT_STATUSES = new Set([502, 504]);
const UPSTREAM_TIMEOUT_MS = 10_000;

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

function upstreamRequest(request, incoming, origin, headers) {
  const upstream = new URL(`${incoming.pathname}${incoming.search}`, origin);
  return new Request(upstream, {
    method: request.method,
    headers,
    body: SAFE_METHODS.has(request.method) ? undefined : request.body,
    duplex: SAFE_METHODS.has(request.method) ? undefined : "half",
    redirect: "manual",
    signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
  });
}

async function completedResponse(request, incoming, origin, headers) {
  const response = await fetch(upstreamRequest(request, incoming, origin, headers));
  if (TRANSPORT_STATUSES.has(response.status)) {
    throw new Error(`Upstream transport status ${response.status}`);
  }
  // Buffer safe reads before returning headers. This catches the production
  // failure where the origin sends HTTP 200 headers but never completes body.
  const body = request.method === "HEAD" ? null : await response.arrayBuffer();
  return { response, body };
}

function proxyResponse(result, requestId) {
  const outgoing = new Headers(result.response.headers);
  outgoing.set("cache-control", "no-store");
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
  async fetch(request, env) {
    const suppliedRequestId = request.headers.get("x-request-id")?.trim();
    const requestId = suppliedRequestId && suppliedRequestId.length <= 128
      ? suppliedRequestId
      : crypto.randomUUID();
    const startedAt = Date.now();
    const incoming = new URL(request.url);
    const headers = new Headers(request.headers);
    headers.delete("authorization");
    headers.delete("x-api-key");
    headers.delete("host");
    headers.set("x-api-key", env.KOYEB_SERVICE_KEY);
    headers.set("x-forwarded-host", incoming.host);
    headers.set("x-forwarded-proto", "https");
    headers.set("x-request-id", requestId);

    try {
      const primary = await completedResponse(request, incoming, primaryOrigin(env), headers);
      console.log(JSON.stringify({
        event: "primary_success", request_id: requestId, method: request.method,
        path: incoming.pathname, status: primary.response.status, duration_ms: Date.now() - startedAt,
      }));
      return proxyResponse(primary, requestId);
    } catch (primaryError) {
      console.error(JSON.stringify({
        event: "primary_failure", request_id: requestId, method: request.method,
        path: incoming.pathname, duration_ms: Date.now() - startedAt,
        error: primaryError instanceof Error ? primaryError.name : "UnknownError",
      }));
      const fallbackOrigin = SAFE_METHODS.has(request.method) ? secondaryOrigin(env) : null;
      if (fallbackOrigin) {
        console.log(JSON.stringify({
          event: "fallback_activation", request_id: requestId, method: request.method, path: incoming.pathname,
        }));
        try {
          const secondary = await completedResponse(request, incoming, fallbackOrigin, headers);
          console.log(JSON.stringify({
            event: "secondary_success", request_id: requestId, method: request.method,
            path: incoming.pathname, status: secondary.response.status, duration_ms: Date.now() - startedAt,
          }));
          return proxyResponse(secondary, requestId);
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
