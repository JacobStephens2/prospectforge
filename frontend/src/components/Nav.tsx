"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import clsx from "clsx";
import { api, type Me } from "@/api/client";
import { Button } from "@/components/ui/button";

const items = [
  { href: "/sources", label: "Sources" },
  { href: "/runs", label: "Runs" },
  { href: "/review", label: "Review" },
  { href: "/suppressions", label: "Suppressions" },
  { href: "/admin/memory", label: "Memory" },
];

export function Nav() {
  const pathname = usePathname();
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    api.me().then(setMe).catch(() => setMe(null));
  }, [pathname]);

  async function logout() {
    try {
      await api.logout();
    } finally {
      router.replace("/login");
    }
  }

  return (
    <nav className="border-b-2 border-brand-blue bg-white">
      <div className="mx-auto max-w-6xl px-4 py-3 flex items-center gap-6">
        <Link href="/" className="font-bold text-brand-blue no-underline text-lg tracking-tight">
          ProspectForge
        </Link>
        <div className="flex gap-1 flex-1">
          {items.map((it) => {
            const active = pathname.startsWith(it.href);
            return (
              <Link
                key={it.href}
                href={it.href}
                className={clsx(
                  "px-3 py-1.5 rounded text-sm font-medium no-underline transition",
                  active
                    ? "bg-brand-blue text-white"
                    : "text-brand-blue hover:bg-brand-gray-light",
                )}
              >
                {it.label}
              </Link>
            );
          })}
        </div>
        <div className="flex items-center gap-3 text-sm">
          {me && (
            <span className="text-brand-blue font-medium hidden sm:inline">
              {me.full_name}
            </span>
          )}
          {/* Always show Sign Out when the Nav renders. The middleware only
              checks cookie presence, not validity — so we may be in a
              "stale cookie" state where /api/auth/me 401s. The user still
              needs a way to clear the cookie and get back to /login. */}
          <Button variant="outline" size="sm" onClick={logout}>
            Sign out
          </Button>
        </div>
      </div>
    </nav>
  );
}
