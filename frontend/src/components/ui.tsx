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
  // CRM's stylesheet: default button is green, delete is red, secondary is
  // blue. We follow the same palette so the apps feel consistent.
  const base =
    "inline-flex items-center justify-center rounded text-sm font-bold px-4 py-1.5 disabled:opacity-50 disabled:cursor-not-allowed transition";
  const variants = {
    primary:
      "bg-brand-green-light text-white hover:bg-brand-green",
    secondary:
      "bg-white border border-brand-gray text-brand-blue hover:bg-brand-gray-light",
    ghost:
      "bg-transparent text-brand-blue-light hover:bg-brand-gray-light",
    danger:
      "bg-brand-red text-white hover:bg-brand-red-dark",
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
        "w-full rounded border border-brand-gray bg-white px-3 py-1.5 text-sm text-brand-blue",
        "placeholder:text-brand-gray focus:outline-none focus:ring-2 focus:ring-brand-blue-light",
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
        "w-full rounded border border-brand-gray bg-white px-3 py-1.5 text-sm text-brand-blue",
        "placeholder:text-brand-gray focus:outline-none focus:ring-2 focus:ring-brand-blue-light",
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
        "rounded border border-brand-gray bg-white px-2 py-1 text-sm text-brand-blue",
        "focus:outline-none focus:ring-2 focus:ring-brand-blue-light",
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
        "rounded-lg border border-brand-gray-light bg-white shadow-sm p-4",
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
  // Tonal palette derived from Acme brand colors. Light-tint background +
  // saturated text. ok=green, info=navy, warn=yellow, bad=red.
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
