import { NextRequest, NextResponse } from "next/server";

// Edge middleware: cheap cookie check that gates the UI. The real
// security boundary is the backend — every /api/* route validates the
// session server-side. This middleware exists for UX (redirect to /login
// when no cookie is present) and to avoid flashing the protected page
// before a client-side guard fires.
//
// Matched routes are everything EXCEPT /login, /verify, and the Next
// internal asset paths.

const COOKIE = "pf_session";

export function middleware(req: NextRequest) {
  const hasSession = req.cookies.has(COOKIE);
  if (!hasSession) {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    url.searchParams.set("next", req.nextUrl.pathname);
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  matcher: [
    // Everything except:
    //   - /login and /verify (pre-auth pages)
    //   - /api/auth/* (the API the login form hits)
    //   - Next's internal asset paths
    //   - any path with a file extension (favicon.ico, .svg, .png, .webmanifest, etc.)
    "/((?!login|verify|api/auth|_next/static|_next/image|.*\\..*).*)",
  ],
};
