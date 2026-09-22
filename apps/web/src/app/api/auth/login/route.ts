import { NextRequest, NextResponse } from "next/server";
import { API_URL, errorJson, forwardHeaders, isSameOrigin } from "@/lib/backend";
import { setSessionCookies, type TokenPair } from "@/lib/session";

export async function POST(req: NextRequest) {
  if (!isSameOrigin(req)) return errorJson(403, "BAD_ORIGIN", "Origin tidak diizinkan");
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return errorJson(400, "BAD_REQUEST", "Body tidak valid");
  }
  const r = await fetch(`${API_URL}/api/v1/auth/login`, {
    method: "POST",
    headers: forwardHeaders(req, { "Content-Type": "application/json" }),
    body: JSON.stringify(body),
    cache: "no-store",
  });
  const data = await r.json().catch(() => null);
  if (!r.ok) return NextResponse.json(data ?? { error: { code: "UPSTREAM", message: "Login gagal" } }, { status: r.status });
  const res = NextResponse.json({ ok: true });
  setSessionCookies(res, data as TokenPair);
  return res;
}
