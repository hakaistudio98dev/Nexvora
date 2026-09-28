import "server-only";
import type { NextRequest, NextResponse } from "next/server";

// Token disimpan di cookie httpOnly: JavaScript di browser tidak bisa membacanya (mitigasi XSS).
// Cookie akses memakai SameSite=Lax agar sesi tetap terbawa saat aplikasi dibuka dari tautan luar
// (mis. WhatsApp) — tanpa Lax, pengguna ponsel selalu terlempar ke halaman login.
// Cookie refresh tetap Strict karena hanya dipakai server (path /api), tidak pernah lewat navigasi.
// Perlindungan CSRF tidak bergantung pada SameSite: setiap request pengubah data dicek Origin-nya.
export const ACCESS_COOKIE = "nx_at";
export const REFRESH_COOKIE = "nx_rt";

export type TokenPair = {
  access_token: string;
  expires_in: number;
  refresh_token: string;
  refresh_expires_in: number;
};

/**
 * Flag `Secure` mengikuti protokol yang benar-benar dipakai pengunjung.
 * Browser menolak cookie Secure di http:// selain localhost — itu yang membuat login dari HP
 * (http://IP-komputer) gagal. COOKIE_SECURE=true memaksa Secure (disarankan di production dengan HTTPS),
 * COOKIE_SECURE=false mematikannya, default "auto".
 */
export function isSecureRequest(req: NextRequest): boolean {
  const mode = (process.env.COOKIE_SECURE ?? "auto").toLowerCase();
  if (mode === "true") return true;
  if (mode === "false") return false;
  const proto = req.headers.get("x-forwarded-proto")?.split(",")[0]?.trim() ?? req.nextUrl.protocol.replace(":", "");
  return proto === "https";
}

export function setSessionCookies(res: NextResponse, t: TokenPair, req: NextRequest) {
  const secure = isSecureRequest(req);
  // JWT-nya sendiri kedaluwarsa cepat (15 menit); cookie dibiarkan hidup selama sesi refresh
  // agar proxy bisa melakukan refresh otomatis.
  res.cookies.set(ACCESS_COOKIE, t.access_token, {
    httpOnly: true, secure, sameSite: "lax", path: "/", maxAge: t.refresh_expires_in,
  });
  res.cookies.set(REFRESH_COOKIE, t.refresh_token, {
    httpOnly: true, secure, sameSite: "strict", path: "/api", maxAge: t.refresh_expires_in,
  });
}

export function clearSessionCookies(res: NextResponse, req: NextRequest) {
  const secure = isSecureRequest(req);
  res.cookies.set(ACCESS_COOKIE, "", { httpOnly: true, secure, sameSite: "lax", path: "/", maxAge: 0 });
  res.cookies.set(REFRESH_COOKIE, "", { httpOnly: true, secure, sameSite: "strict", path: "/api", maxAge: 0 });
}
