import { apiOrigin, PROXY_ROUTES, proxyUrl, type ProxyRoute } from "@/lib/proxy";

export const dynamic = "force-dynamic";

const BODY_LIMIT = 8192;

async function forward(request: Request, route: string): Promise<Response> {
  const method = PROXY_ROUTES[route as ProxyRoute];
  if (!method || request.method !== method) {
    return Response.json({ detail: "unknown route" }, { status: 404 });
  }
  const origin = apiOrigin(process.env.QUANTFORGE_API_ORIGIN, process.env.NODE_ENV);
  if (!origin) {
    return Response.json(
      { detail: "QUANTFORGE_API_ORIGIN is not set. The terminal will not call localhost in production." },
      { status: 503 },
    );
  }
  const target = proxyUrl(origin, route, new URL(request.url).search);
  if (!target) return Response.json({ detail: "unknown route" }, { status: 404 });
  const headers = new Headers({ accept: "application/json" });
  let body: string | undefined;
  if (method === "POST") {
    body = await request.text();
    if (body.length > BODY_LIMIT) {
      return Response.json({ detail: "request body exceeds 8 KB" }, { status: 413 });
    }
    headers.set("content-type", "application/json");
  }
  try {
    const upstream = await fetch(target, { method, headers, body, cache: "no-store" });
    const text = await upstream.text();
    return new Response(text, {
      status: upstream.status,
      headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return Response.json({ detail: "QuantForge API is unavailable" }, { status: 502 });
  }
}

async function routeName(context: { params: Promise<{ path: string[] }> }): Promise<string> {
  const { path } = await context.params;
  return path.length === 1 ? path[0] : "";
}

export async function GET(request: Request, context: { params: Promise<{ path: string[] }> }) {
  return forward(request, await routeName(context));
}

export async function POST(request: Request, context: { params: Promise<{ path: string[] }> }) {
  return forward(request, await routeName(context));
}
