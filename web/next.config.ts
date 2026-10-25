import type { NextConfig } from "next";

// Headers for every response. The Content-Security-Policy (with a fresh nonce
// per request) is set in proxy.ts, since it can't be static.
const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "same-origin" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Permissions-Policy", value: "camera=(), geolocation=(), microphone=(self)" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
];

// The desktop app serves exported files from its backend (no Node there):
// see scripts/build-desktop.mjs. Its backend sets the security headers.
const desktop: NextConfig = {
  output: "export",
  poweredByHeader: false,
  reactStrictMode: true,
  images: { unoptimized: true },
};

const server: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  async headers() {
    return [
      { source: "/:path*", headers: securityHeaders },
      {
        source: "/sw.js",
        headers: [
          { key: "Cache-Control", value: "no-cache, no-store, must-revalidate" },
          { key: "Content-Type", value: "application/javascript; charset=utf-8" },
        ],
      },
    ];
  },
};

export default process.env.NEXT_PUBLIC_RINGKEEPER_TARGET === "desktop" ? desktop : server;
