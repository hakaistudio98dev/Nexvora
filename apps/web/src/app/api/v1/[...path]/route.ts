/**
 * BFF proxy: browser → /api/v1/* (Next.js) → FastAPI /api/v1/*.
 * - Access token dibaca dari cookie httpOnly dan dipasang sebagai Bearer.
 * - Saat API membalas 401, proxy mencoba refresh sekali (rotasi token) lalu mengulang request.
 * - Endpoint token (login/refresh/logout) tidak bisa diakses lewat proxy ini.
 */
import { NextRequest, NextResponse } from "next/server";
import { API_URL, errorJson, forwardHeaders, isSameOrigin } from "@/lib/backend";
import { ACCESS_COOKIE, REFRESH_COOKIE, clearSessionCookies, setSessionCookies, type TokenPair } from "@/lib/session";

const BLOCKED = new Set(["auth/login", "auth/refresh", "auth/logout"]);
const SAFE_SEGMENT = /^[A-Za-z0-9._-]+$/;

async function handle(req: NextRequest, { params }: { params: { path: string[] } }) {
  const segments = params.path ?? [];
  if (!segments.every((s) => SAFE_SEGMENT.test(s) && s !== ".." && s !== ".")) {
    return errorJson(400, "BAD_PATH", "Path tidak valid");
  }
  const path = segments.join("/");
  if (BLOCKED.has(path)) return errorJson(404, "NOT_FOUND", "Tidak ditemukan");
  if (!isSameOrigin(req)) return errorJson(403, "BAD_ORIGIN", "Origin tidak diizinkan");

  const url = `${API_URL}/api/v1/${path}${req.nextUrl.search}`;
  const body = ["GET", "HEAD"].includes(req.method) ? undefined : await req.text();

  const call = (token?: string) => {
    const h = forwardHeaders(req, { "Content-Type": "application/json" });
    if (token) h.set("Authorization", `Bearer ${token}`);
    const idem = req.headers.get("idempotency-key");
    if (idem) h.set("Idempotency-Key", idem);
    return fetch(url, { method: req.method, headers: h, body, cache: "no-store" });
  };

  let upstream = await call(req.cookies.get(ACCESS_COOKIE)?.value);
  let refreshed: TokenPair | null = null;

  if (upstream.status === 401) {
    const rt = req.cookies.get(REFRESH_COOKIE)?.value;
    if (rt) {
      const r = await fetch(`${API_URL}/api/v1/auth/refresh`, {
        method: "POST",
        headers: forwardHeaders(req, { "Content-Type": "application/json" }),
        body: JSON.stringify({ refresh_token: rt }),
        cache: "no-store",
      });
      if (r.ok) {
        refreshed = (await r.json()) as TokenPair;
        upstream = await call(refreshed.access_token);
      }
    }
  }

  const text = upstream.status === 204 ? null : await upstream.text();
  const res = new NextResponse(text, {
    status: upstream.status,
    headers: {
      "Content-Type": upstream.headers.get("content-type") ?? "application/json",
      "X-Correlation-ID": upstream.headers.get("x-correlation-id") ?? "",
      ...(upstream.headers.get("idempotent-replayed") ? { "Idempotent-Replayed": "true" } : {}),
      ...(upstream.headers.get("content-disposition") ? { "Content-Disposition": upstream.headers.get("content-disposition")! } : {}),
      "Cache-Control": "no-store",
    },
  });
  if (refreshed) setSessionCookies(res, refreshed);
  else if (upstream.status === 401) clearSessionCookies(res);
  return res;
}

export { handle as GET, handle as POST, handle as PATCH, handle as DELETE };
