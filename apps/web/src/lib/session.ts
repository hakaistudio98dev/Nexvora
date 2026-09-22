import "server-only";
import type { NextResponse } from "next/server";

// Token disimpan di cookie httpOnly: JavaScript di browser tidak bisa membacanya (mitigasi XSS),
// SameSite=Strict + pengecekan Origin di proxy (mitigasi CSRF).
export const ACCESS_COOKIE = "nx_at";
export const REFRESH_COOKIE = "nx_rt";

const secure = process.env.NODE_ENV === "production";

export type TokenPair = {
  access_token: string;
  expires_in: number;
  refresh_token: string;
  refresh_expires_in: number;
};

export function setSessionCookies(res: NextResponse, t: TokenPair) {
  // JWT-nya sendiri kedaluwarsa cepat (15 menit); cookie dibiarkan hidup selama sesi refresh
  // agar proxy bisa melakukan refresh otomatis.
  res.cookies.set(ACCESS_COOKIE, t.access_token, {
    httpOnly: true, secure, sameSite: "strict", path: "/", maxAge: t.refresh_expires_in,
  });
  res.cookies.set(REFRESH_COOKIE, t.refresh_token, {
    httpOnly: true, secure, sameSite: "strict", path: "/api", maxAge: t.refresh_expires_in,
  });
}

export function clearSessionCookies(res: NextResponse) {
  res.cookies.set(ACCESS_COOKIE, "", { httpOnly: true, secure, sameSite: "strict", path: "/", maxAge: 0 });
  res.cookies.set(REFRESH_COOKIE, "", { httpOnly: true, secure, sameSite: "strict", path: "/api", maxAge: 0 });
}
