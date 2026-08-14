/* global URL, Response, fetch, Request, Headers */

const PAGES_ORIGIN = "https://bankrotai.pages.dev";
const PRIMARY_HOST = "dezster.ru";

export default {
  async fetch(request) {
    const incoming = new URL(request.url);

    if (incoming.protocol !== "https:" || incoming.hostname === `www.${PRIMARY_HOST}`) {
      const canonical = new URL(`${incoming.pathname}${incoming.search}`, `https://${PRIMARY_HOST}`);
      return Response.redirect(canonical.toString(), 308);
    }

    const upstream = new URL(`${incoming.pathname}${incoming.search}`, PAGES_ORIGIN);
    const upstreamHeaders = new Headers(request.headers);
    upstreamHeaders.delete("host");
    const upstreamRequest = new Request(upstream, {
      method: request.method,
      headers: upstreamHeaders,
      body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
      redirect: "manual",
    });
    const response = await fetch(upstreamRequest);
    const headers = new Headers(response.headers);
    const location = headers.get("location");

    if (location) {
      const redirect = new URL(location, upstream);
      if (redirect.hostname === "bankrotai.pages.dev") {
        redirect.hostname = PRIMARY_HOST;
        headers.set("location", redirect.toString());
      }
    }

    headers.set("x-bankrotai-edge", "cloudflare");
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers,
    });
  },
};
