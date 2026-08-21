/* global Headers, Request, Response, URL, fetch */

const ORIGIN = "https://194-226-126-233.sslip.io";

export default {
  async fetch(request, env) {
    const incoming = new URL(request.url);
    const upstream = new URL(`${incoming.pathname}${incoming.search}`, ORIGIN);
    const headers = new Headers(request.headers);
    headers.delete("authorization");
    headers.delete("x-api-key");
    headers.delete("host");
    headers.set("x-api-key", env.KOYEB_SERVICE_KEY);
    headers.set("x-forwarded-host", incoming.host);
    headers.set("x-forwarded-proto", "https");

    try {
      const response = await fetch(new Request(upstream, {
        method: request.method,
        headers,
        body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
        redirect: "manual",
      }));
      const outgoing = new Headers(response.headers);
      outgoing.set("cache-control", "no-store");
      outgoing.set("x-content-type-options", "nosniff");
      outgoing.set("referrer-policy", "same-origin");
      return new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers: outgoing,
      });
    } catch {
      return Response.json({ detail: "API upstream is temporarily unavailable" }, {
        status: 502,
        headers: { "cache-control": "no-store" },
      });
    }
  },
};
