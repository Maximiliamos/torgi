interface Env {
  TUNNEL_REGISTRY: { put(key: string, value: string): Promise<void> };
  TUNNEL_REGISTRATION_SECRET: string;
}

interface PagesContext {
  request: Request;
  env: Env;
}

function isTunnelOrigin(value: unknown): value is string {
  if (typeof value !== "string") return false;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.port === "" &&
      url.hostname.endsWith(".trycloudflare.com");
  } catch {
    return false;
  }
}

export async function onRequestPost(context: PagesContext): Promise<Response> {
  if (context.request.headers.get("authorization") !==
      `Bearer ${context.env.TUNNEL_REGISTRATION_SECRET}`) {
    return new Response("Not found", { status: 404 });
  }
  const payload = await context.request.json().catch(() => null) as { origin?: unknown } | null;
  if (!isTunnelOrigin(payload?.origin)) {
    return Response.json({ detail: "Invalid tunnel origin" }, { status: 400 });
  }
  await context.env.TUNNEL_REGISTRY.put("active-origin", payload.origin);
  return Response.json({ status: "registered" });
}
