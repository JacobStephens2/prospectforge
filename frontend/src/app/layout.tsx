import "../styles/globals.css";
import type { Metadata } from "next";
import { Nav } from "@/components/Nav";
import { Geist } from "next/font/google";
import { cn } from "@/lib/utils";

const geist = Geist({subsets:['latin'],variable:'--font-sans'});

export const metadata: Metadata = {
  title: "ProspectForge",
  description: "Acme's contact-sourcing tool",
  icons: {
    icon: [{ url: "/favicon.svg", type: "image/svg+xml" }],
  },
  manifest: "/site.webmanifest",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={cn("font-sans", geist.variable)}>
      <body>
        {/* Nav reads /api/auth/me itself; it renders empty user state on
            /login and /verify, which is the right UX. The middleware
            ensures unauthenticated users only see those two paths. */}
        <Nav />
        <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
