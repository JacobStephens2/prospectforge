import "../styles/globals.css";
import type { Metadata } from "next";
import { Nav } from "@/components/Nav";

export const metadata: Metadata = {
  title: "ProspectForge",
  description: "Acme's contact-sourcing tool",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
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
