interface Env {
  KOYEB_API_ORIGIN: string;
  KOYEB_SERVICE_KEY: string;
  TUNNEL_REGISTRY: { get(key: string): Promise<string | null> };
}

interface PagesContext {
  request: Request;
  env: Env;
}

export async function onRequest(context: PagesContext): Promise<Response> {
  const registeredOrigin = await context.env.TUNNEL_REGISTRY.get("active-origin");
  // A configured production origin is authoritative. The registry remains only
  // as a compatibility fallback for installations still using a quick tunnel.
  const configuredOrigin = context.env.KOYEB_API_ORIGIN?.trim();
  const origin = new URL(configuredOrigin || registeredOrigin || "https://invalid.invalid");
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

  const method = context.request.method.toUpperCase();
  const isIdempotent = ["GET", "HEAD"].includes(method);
  const isExpensiveExternalRead =
    incoming.pathname.startsWith("/api/search/") ||
    incoming.pathname.startsWith("/api/online/") ||
    incoming.pathname === "/api/cadastre/search";
  // External catalogues are idempotent but not cheaply cancellable: retrying a
  // timed-out thread doubles load while the first source request is still
  // running. Give it one bounded attempt instead.
  const attempts = isIdempotent && !isExpensiveExternalRead ? 2 : 1;
  const timeoutMs = isExpensiveExternalRead ? 35_000 : 12_000;

  try {
    let response: Response | undefined;
    let lastError: unknown;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      try {
        response = await fetch(upstream, {
          method,
          headers,
          body: isIdempotent ? undefined : context.request.body,
          redirect: "manual",
          signal: AbortSignal.timeout(timeoutMs),
        });
        if (![502, 503, 504].includes(response.status) || attempt === attempts - 1) {
          break;
        }
        await response.body?.cancel();
      } catch (error) {
        lastError = error;
        if (attempt === attempts - 1) {
          throw error;
        }
      }
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    if (!response) {
      throw lastError || new Error("API upstream did not return a response");
    }
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
