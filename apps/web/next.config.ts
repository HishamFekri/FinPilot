import path from "node:path";
import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";
import { resolveApiProxyTarget } from "./src/lib/api-proxy-target";

const apiProxyTarget = resolveApiProxyTarget(process.env.API_PROXY_TARGET);
// The container image sets this: a self-contained server with only the files it needs. Local
// development and `next start` stay as they were.
const standalone = process.env.FINPILOT_STANDALONE === "1";

const nextConfig: NextConfig = {
  poweredByHeader: false,
  ...(standalone
    ? {
        output: "standalone" as const,
        // npm workspaces hoist packages to the repository root, so tracing starts there.
        outputFileTracingRoot: path.join(__dirname, "..", ".."),
      }
    : {}),
  agentRules: false,
  // Experimental, but documented for root layouts under a dynamic segment like [locale].
  // See src/app/global-not-found.tsx for why it's needed.
  experimental: { globalNotFound: true },
  turbopack: {
    rules: {
      // Only the Tailwind entry file goes through Tailwind's loader. A broader "*.css" rule also
      // caught CSS modules and stripped their class exports.
      "globals.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  // The policy itself is per response, with a nonce, so it lives in src/proxy.ts.
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
          ...(process.env.NODE_ENV === "production"
            ? [
                {
                  key: "Strict-Transport-Security",
                  value: "max-age=31536000; includeSubDomains",
                },
              ]
            : []),
          {
            key: "Permissions-Policy",
            value:
              "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
          },
        ],
      },
    ];
  },
  // The browser only ever calls its own origin, so the web app needs no CORS (ARCHITECTURE.md §1).
  async rewrites() {
    return [{ source: "/api/v1/:path*", destination: `${apiProxyTarget}/api/v1/:path*` }];
  },
};

export default createNextIntlPlugin("./src/i18n/request.ts")(nextConfig);
