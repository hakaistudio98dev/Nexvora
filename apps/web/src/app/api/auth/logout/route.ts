import { NextRequest, NextResponse } from "next/server";
import { API_URL, errorJson, forwardHeaders, isSameOrigin } from "@/lib/backend";
import { REFRESH_COOKIE, clearSessionCookies } from "@/lib/session";

export async function POST(req: NextRequest) {
  if (!isSameOrigin(req)) return errorJson(403, "BAD_ORIGIN", "Origin tidak diizinkan");
  const rt = req.cookies.get(REFRESH_COOKIE)?.value;
  if (rt) {
    await fetch(`${API_URL}/api/v1/auth/logout`, {
      method: "POST",
      headers: forwardHeaders(req, { "Content-Type": "application/json" }),
      body: JSON.stringify({ refresh_token: rt }),
      cache: "no-store",
    }).catch(() => undefined);
  }
  const res = NextResponse.json({ ok: true });
  clearSessionCookies(res);
  return res;
}
