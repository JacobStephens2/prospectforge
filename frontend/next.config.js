/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  async rewrites() {
    // Proxy /api/* to the backend so the browser doesn't need CORS.
    // Service name is stable in our docker-compose stack; for non-compose dev
    // (rare — frontend talking to a remote backend) override via BACKEND_URL.
    // Standalone-mode Next evaluates this at build, so env interpolation needs
    // a build-arg if you want runtime override (out of scope for v1).
    const backend = process.env.BACKEND_URL || "http://backend:8000";
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};
module.exports = nextConfig;
