export const PROXY_ROUTES = {
  health: "GET",
  config: "GET",
  backtest: "POST",
  compare: "POST",
  "research-block": "GET",
} as const;

export type ProxyRoute = keyof typeof PROXY_ROUTES;

export function apiOrigin(env: string | undefined, nodeEnv: string | undefined): string | null {
  const configured = env?.trim().replace(/\/$/, "");
  if (configured) return configured;
  if (nodeEnv === "development") return "http://127.0.0.1:8000";
  return null;
}

export function proxyTarget(origin: string, route: string): string | null {
  if (!(route in PROXY_ROUTES)) return null;
  return `${origin.replace(/\/$/, "")}/api/${route}`;
}

export function proxyUrl(origin: string, route: string, search = ""): string | null {
  const target = proxyTarget(origin, route);
  if (target === null) return null;
  return search === "" ? target : `${target}${search}`;
}
