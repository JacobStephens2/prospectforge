"use client";

import clsx from "clsx";
import type { ReactNode } from "react";

/**
 * Acme-brand-tinted Badge. shadcn's Badge has only default/secondary/
 * destructive/outline — we need semantic intents (ok / warn / bad / info /
 * neutral) for the review queue's at-a-glance scanning. Keeping our custom
 * one rather than shoehorning into shadcn's variants.
 */
export function Badge({
  children,
  intent = "neutral",
}: {
  children: ReactNode;
  intent?: "neutral" | "ok" | "warn" | "bad" | "info";
}) {
  const intents = {
    neutral: "bg-brand-gray-light text-brand-blue",
    ok: "bg-brand-green-light/15 text-brand-green",
    warn: "bg-brand-yellow/30 text-brand-blue",
    bad: "bg-brand-red/15 text-brand-red-dark",
    info: "bg-brand-blue-light/15 text-brand-blue-light",
  };
  return (
    <span
      className={clsx(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-bold",
        intents[intent],
      )}
    >
      {children}
    </span>
  );
}
