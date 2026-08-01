interface Env {
  KOYEB_API_ORIGIN: string;
  KOYEB_SERVICE_KEY: string;
}

interface PagesContext {
  request: Request;
  env: Env;
}

export async function onRequest(context: PagesContext): Promise<Response> {
  const origin = new URL(context.env.KOYEB_API_ORIGIN);
  if (origin.protocol !== "https:") {
    return Response.json({ detail: "Upstream API configuration is invalid" }, { status: 503 });
  }
  const incoming = new URL(context.request.url);
  const upstream = new URL(`${incoming.pathname}${incoming.search}`, origin);
  const headers = new Headers(context.request.headers);
  headers.delete("authorization");
  headers.delete("x-api-key");
  headers.delete("host");
  headers.set("x-api-key", context.env.KOYEB_SERVICE_KEY);
  headers.set("x-forwarded-host", incoming.host);
  headers.set("x-forwarded-proto", "https");

  try {
    const response = await fetch(upstream, {
      method: context.request.method,
      headers,
      body: ["GET", "HEAD"].includes(context.request.method) ? undefined : context.request.body,
      redirect: "manual",
      signal: AbortSignal.timeout(15_000),
    });
    const outgoingHeaders = new Headers(response.headers);
    outgoingHeaders.set("cache-control", "no-store");
    outgoingHeaders.set("x-content-type-options", "nosniff");
    outgoingHeaders.set("referrer-policy", "same-origin");
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: outgoingHeaders,
    });
  } catch {
    return Response.json({ detail: "API upstream is temporarily unavailable" }, {
      status: 502,
      headers: { "cache-control": "no-store" },
    });
  }
}
