/* global AbortSignal, Headers, Request, Response, URL, console, crypto, fetch */

import { Container, getContainer } from "@cloudflare/containers";

const PRIMARY_ORIGIN = "https://194-226-126-233.sslip.io";
const SAFE_METHODS = new Set(["GET", "HEAD"]);
const TRANSPORT_STATUSES = new Set([502, 504]);
const UPSTREAM_TIMEOUT_MS = 10_000;

export class BankrotAISecondary extends Container {
  defaultPort = 8000;
  requiredPorts = [8000];
  sleepAfter = "2h";
  enableInternet = true;
}

function normalizedSecondary(env) {
  const value = env.SECONDARY_API_ORIGIN?.trim();
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" ? url.origin : null;
  } catch {
    return null;
  }
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

async function completedContainerResponse(request, headers, env) {
  if (!env.BANKROTAI_SECONDARY || !env.NEON_DATABASE_URL || !env.AUTH_SESSION_SECRET) {
    return null;
  }
  const container = getContainer(env.BANKROTAI_SECONDARY, "production-api");
  await container.startAndWaitForPorts({
    ports: 8000,
    startOptions: {
      envVars: {
        APP_ENV: "production",
        API_READ_ONLY: "true",
        API_RATE_LIMIT_PER_MINUTE: "60",
        DATABASE_URL: env.NEON_DATABASE_URL,
        BANKROTAI_API_KEY: env.KOYEB_SERVICE_KEY,
        AUTH_SESSION_SECRET: env.AUTH_SESSION_SECRET,
        CORS_ORIGINS: "https://dezster.ru,https://bankrotai.pages.dev",
      },
    },
    cancellationOptions: { portReadyTimeoutMS: 20_000, instanceGetTimeoutMS: 8_000 },
  });
  const response = await container.fetch(new Request(request.url, {
    method: request.method,
    headers,
    redirect: "manual",
    signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
  }));
  if (TRANSPORT_STATUSES.has(response.status)) {
    throw new Error(`Secondary transport status ${response.status}`);
  }
  const body = request.method === "HEAD" ? null : await response.arrayBuffer();
  return { response, body };
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
      const primary = await completedResponse(request, incoming, PRIMARY_ORIGIN, headers);
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
      const secondaryOrigin = SAFE_METHODS.has(request.method) ? normalizedSecondary(env) : null;
      const containerEnabled = SAFE_METHODS.has(request.method) && env.BANKROTAI_SECONDARY;
      if (secondaryOrigin || containerEnabled) {
        console.log(JSON.stringify({
          event: "fallback_activation", request_id: requestId, method: request.method, path: incoming.pathname,
        }));
        try {
          const secondary = secondaryOrigin
            ? await completedResponse(request, incoming, secondaryOrigin, headers)
            : await completedContainerResponse(request, headers, env);
          if (!secondary) throw new Error("Secondary is not configured");
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
