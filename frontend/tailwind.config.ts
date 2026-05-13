import type { Config } from "tailwindcss";

/**
 * Acme brand palette as Tailwind tokens. Two layers:
 *   - `brand.*` — direct named Acme colors (brand-blue, brand-green, etc.).
 *               Used for Acme-specific styling that doesn't fit shadcn's
 *               semantic vocab (e.g., the Nav's border-brand-blue accent,
 *               our Badge's tonal palette).
 *   - shadcn semantic tokens (primary, destructive, border, …) — composed
 *               from CSS variables defined in globals.css. shadcn's
 *               components (Button, Input, Card, …) reference these.
 *
 * Source of truth for Acme HSL values: the legacy CRM stylesheet.
 */
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          "Work Sans",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      colors: {
        // shadcn semantic tokens — read CSS vars from globals.css :root.
        border: "hsl(var(--border) / <alpha-value>)",
        input: "hsl(var(--input) / <alpha-value>)",
        ring: "hsl(var(--ring) / <alpha-value>)",
        background: "hsl(var(--background) / <alpha-value>)",
        foreground: "hsl(var(--foreground) / <alpha-value>)",
        primary: {
          DEFAULT: "hsl(var(--primary) / <alpha-value>)",
          foreground: "hsl(var(--primary-foreground) / <alpha-value>)",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary) / <alpha-value>)",
          foreground: "hsl(var(--secondary-foreground) / <alpha-value>)",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive) / <alpha-value>)",
          foreground: "hsl(var(--destructive-foreground) / <alpha-value>)",
        },
        muted: {
          DEFAULT: "hsl(var(--muted) / <alpha-value>)",
          foreground: "hsl(var(--muted-foreground) / <alpha-value>)",
        },
        accent: {
          DEFAULT: "hsl(var(--accent) / <alpha-value>)",
          foreground: "hsl(var(--accent-foreground) / <alpha-value>)",
        },
        popover: {
          DEFAULT: "hsl(var(--popover) / <alpha-value>)",
          foreground: "hsl(var(--popover-foreground) / <alpha-value>)",
        },
        card: {
          DEFAULT: "hsl(var(--card) / <alpha-value>)",
          foreground: "hsl(var(--card-foreground) / <alpha-value>)",
        },

        // Acme brand tokens — explicit, used by Nav and Badge.
        brand: {
          blue: "hsl(208.5 100% 15.69%)",
          "blue-light": "hsl(208.5 100% 29%)",
          green: "hsl(140 50% 30%)",
          "green-light": "hsl(140 50% 35%)",
          red: "hsl(0.72 66.94% 51.37%)",
          "red-dark": "hsl(1 64% 47%)",
          yellow: "hsl(60 100% 51%)",
          gray: "hsl(0 0% 53%)",
          "gray-light": "hsl(0 0% 94%)",
          surface: "hsl(0 0% 97%)",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
    },
  },
  plugins: [],
};
export default config;
