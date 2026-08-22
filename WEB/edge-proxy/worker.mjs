/* global URL, Response, fetch, Request, Headers */

const PAGES_ORIGIN = "https://bankrotai.pages.dev";
const API_ORIGIN = "https://api.dezster.ru";
const PRIMARY_HOST = "dezster.ru";
const SECURITY_HEADERS = {
  "strict-transport-security": "max-age=31536000; includeSubDomains",
  "x-content-type-options": "nosniff",
  "x-frame-options": "DENY",
  "referrer-policy": "same-origin",
};

function withSecurityHeaders(headers = new Headers()) {
  for (const [name, value] of Object.entries(SECURITY_HEADERS)) {
    headers.set(name, value);
  }
  headers.set("x-bankrotai-edge", "cloudflare");
  return headers;
}

function unavailableResponse(incoming) {
  const headers = withSecurityHeaders(new Headers({
    "cache-control": "no-store",
    "retry-after": "5",
  }));
  if (incoming.pathname.startsWith("/api/")) {
    headers.set("content-type", "application/json; charset=utf-8");
    return new Response(JSON.stringify({ detail: "Service temporarily unavailable" }), {
      status: 503,
      headers,
    });
  }
  headers.set("content-type", "text/html; charset=utf-8");
  return new Response(
    "<!doctype html><html lang=\"ru\"><meta charset=\"utf-8\"><title>BankrotAI временно недоступен</title><main><h1>Сервис временно недоступен</h1><p>Повторите попытку через несколько секунд.</p></main></html>",
    { status: 503, headers },
  );
}

export default {
  async fetch(request, env) {
    const incoming = new URL(request.url);

    if (incoming.protocol !== "https:" || incoming.hostname === `www.${PRIMARY_HOST}`) {
      const canonical = new URL(`${incoming.pathname}${incoming.search}`, `https://${PRIMARY_HOST}`);
      return new Response(null, {
        status: 308,
        headers: withSecurityHeaders(new Headers({ location: canonical.toString() })),
      });
    }

    // API requests already have a dedicated Worker route. Sending them through
    // Pages adds an unnecessary proxy hop and caused observed 41-second stalls.
    const origin = incoming.pathname.startsWith("/api/") ? API_ORIGIN : PAGES_ORIGIN;
    const upstream = new URL(`${incoming.pathname}${incoming.search}`, origin);
    const upstreamHeaders = new Headers(request.headers);
    upstreamHeaders.delete("host");
    const upstreamRequest = new Request(upstream, {
      method: request.method,
      headers: upstreamHeaders,
      body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
      redirect: "manual",
    });
    let response;
    try {
      response = incoming.pathname.startsWith("/api/")
        ? await env.API_PROXY.fetch(upstreamRequest)
        : await fetch(
          upstreamRequest,
          incoming.pathname === "/deployment.json" ? { cache: "no-store" } : undefined,
        );
    } catch {
      return unavailableResponse(incoming);
    }
    const headers = withSecurityHeaders(new Headers(response.headers));
    const location = headers.get("location");

    if (location) {
      const redirect = new URL(location, upstream);
      if (redirect.hostname === "bankrotai.pages.dev" || redirect.hostname === "api.dezster.ru") {
        redirect.hostname = PRIMARY_HOST;
        headers.set("location", redirect.toString());
      }
    }

    if (incoming.pathname === "/deployment.json") {
      headers.set("cache-control", "no-store");
    }
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers,
    });
  },
};
