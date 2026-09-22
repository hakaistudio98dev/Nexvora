import { NextRequest, NextResponse } from "next/server";

// Guard ringan: halaman aplikasi butuh cookie sesi. Validasi token sebenarnya tetap di API.
const PROTECTED = ["/dashboard", "/orders", "/inventory", "/wms", "/scan", "/billing", "/api-keys", "/platform", "/shipping", "/returns", "/label", "/analytics", "/noc", "/notifications", "/settings", "/products", "/warehouses", "/users", "/audit"];

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  const hasSession = req.cookies.has("nx_at");
  if (PROTECTED.some((p) => pathname.startsWith(p)) && !hasSession) {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    url.searchParams.set("next", pathname);
    return NextResponse.redirect(url);
  }
  if ((pathname === "/login" || pathname === "/signup") && req.cookies.has("nx_at")) {
    return NextResponse.redirect(new URL("/dashboard", req.url));
  }
  return NextResponse.next();
}

export const config = { matcher: ["/((?!api|_next|favicon.ico).*)"] };
