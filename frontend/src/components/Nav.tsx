"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";

const items = [
  { href: "/sources", label: "Sources" },
  { href: "/runs", label: "Runs" },
  { href: "/review", label: "Review" },
  { href: "/suppressions", label: "Suppressions" },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <nav className="border-b border-neutral-200 bg-white">
      <div className="mx-auto max-w-6xl px-4 py-3 flex items-center gap-6">
        <Link href="/" className="font-semibold text-neutral-900 no-underline">
          ProspectForge
        </Link>
        <div className="flex gap-1">
          {items.map((it) => {
            const active = pathname.startsWith(it.href);
            return (
              <Link
                key={it.href}
                href={it.href}
                className={clsx(
                  "px-3 py-1.5 rounded-md text-sm no-underline transition",
                  active
                    ? "bg-blue-100 text-blue-900"
                    : "text-neutral-700 hover:bg-neutral-100",
                )}
              >
                {it.label}
              </Link>
            );
          })}
        </div>
      </div>
    </nav>
  );
}
