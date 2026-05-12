/**
 * Minimal Tailwind-styled primitives. These are deliberately tiny replacements
 * for shadcn/ui until someone with an interactive shell can run
 * `npx shadcn-ui@latest init` and swap them out. Keep the API close to
 * shadcn's so the swap is mechanical.
 */
"use client";

import clsx from "clsx";
import type {
  ButtonHTMLAttributes,
  HTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";

export function Button({
  className,
  variant = "primary",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
}) {
  const base = "inline-flex items-center justify-center rounded-md text-sm font-medium px-3 py-1.5 disabled:opacity-50 disabled:cursor-not-allowed transition";
  const variants = {
    primary: "bg-blue-600 text-white hover:bg-blue-700",
    secondary: "bg-white border border-neutral-300 text-neutral-900 hover:bg-neutral-100",
    ghost: "bg-transparent text-neutral-700 hover:bg-neutral-100",
    danger: "bg-red-600 text-white hover:bg-red-700",
  };
  return <button className={clsx(base, variants[variant], className)} {...props} />;
}

export function Input({
  className,
  ...props
}: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={clsx(
        "w-full rounded-md border border-neutral-300 bg-white px-3 py-1.5 text-sm",
        "placeholder:text-neutral-400 focus:outline-none focus:ring-2 focus:ring-blue-500",
        className,
      )}
      {...props}
    />
  );
}

export function Textarea({
  className,
  ...props
}: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={clsx(
        "w-full rounded-md border border-neutral-300 bg-white px-3 py-1.5 text-sm",
        "placeholder:text-neutral-400 focus:outline-none focus:ring-2 focus:ring-blue-500",
        className,
      )}
      {...props}
    />
  );
}

export function Select({
  className,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={clsx(
        "rounded-md border border-neutral-300 bg-white px-2 py-1 text-sm",
        "focus:outline-none focus:ring-2 focus:ring-blue-500",
        className,
      )}
      {...props}
    />
  );
}

export function Card({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={clsx(
        "rounded-lg border border-neutral-200 bg-white shadow-sm p-4",
        className,
      )}
      {...props}
    />
  );
}

export function Badge({
  children,
  intent = "neutral",
}: {
  children: ReactNode;
  intent?: "neutral" | "ok" | "warn" | "bad" | "info";
}) {
  const intents = {
    neutral: "bg-neutral-100 text-neutral-700",
    ok: "bg-green-100 text-green-800",
    warn: "bg-amber-100 text-amber-800",
    bad: "bg-red-100 text-red-800",
    info: "bg-blue-100 text-blue-800",
  };
  return (
    <span
      className={clsx(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
        intents[intent],
      )}
    >
      {children}
    </span>
  );
}

export function Label({
  htmlFor,
  children,
}: {
  htmlFor?: string;
  children: ReactNode;
}) {
  return (
    <label htmlFor={htmlFor} className="block text-xs font-medium text-neutral-700 mb-1">
      {children}
    </label>
  );
}
