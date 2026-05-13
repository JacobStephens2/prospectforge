"use client";

import clsx from "clsx";
import type { SelectHTMLAttributes } from "react";

/**
 * Plain native `<select>` with Acme styling. shadcn's Select is built on
 * Radix's controlled primitive (SelectTrigger / SelectContent / SelectItem)
 * which is overkill for our short dropdowns and would require rewriting
 * every page that uses options-as-children. This keeps the migration small.
 */
export function Select({
  className,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={clsx(
        "rounded border border-input bg-background px-2 py-1 text-sm text-foreground",
        "focus:outline-none focus:ring-2 focus:ring-ring",
        className,
      )}
      {...props}
    />
  );
}
