import type { Config } from "tailwindcss";

// Acme brand palette. Authoritative source is the legacy CRM stylesheet.
// Hex values are conversions of the HSL declarations there.
//   blue:        hsl(208.5 100% 15.69%)  →  #003e80-ish dark navy
//   blue-light:  hsl(208.5 100%  29%)    →  #00568d
//   green:       hsl(140    50%  30%)    →  #267349 brand-button base
//   green-light: hsl(140    50%  35%)    →  #2d8049
//   red:         hsl(0.72   66.94% 51.37%)
//   gray:        hsl(0      0%   53%)
//   off-white:   hsl(0      0%   97%)
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
    },
  },
  plugins: [],
};
export default config;
