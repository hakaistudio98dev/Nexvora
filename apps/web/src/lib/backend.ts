import "server-only";
import type { NextRequest } from "next/server";

export const API_URL = (process.env.API_INTERNAL_URL ?? "http://localhost:8000").replace(/\/$/, "");

/** Header yang diteruskan ke API: correlation id + IP klien untuk audit & rate limit. */
export function forwardHeaders(req: NextRequest, extra: Record<string, string> = {}): Headers {
  const h = new Headers(extra);
  const cid = req.headers.get("x-correlation-id") ?? crypto.randomUUID().replace(/-/g, "");
  h.set("X-Correlation-ID", cid);
  const ip = req.headers.get("x-real-ip") ?? req.headers.get("x-forwarded-for")?.split(",")[0]?.trim();
  if (ip) h.set("X-Real-IP", ip);
  const ua = req.headers.get("user-agent");
  if (ua) h.set("User-Agent", ua);
  return h;
}

/** Tolak request yang mengubah data jika datang dari origin lain (CSRF). */
export function isSameOrigin(req: NextRequest): boolean {
  if (["GET", "HEAD", "OPTIONS"].includes(req.method)) return true;
  const origin = req.headers.get("origin");
  if (!origin) return false;
  const host = req.headers.get("x-forwarded-host") ?? req.headers.get("host");
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

export function errorJson(status: number, code: string, message: string) {
  return Response.json({ error: { code, message } }, { status });
}
