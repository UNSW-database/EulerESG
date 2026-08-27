/** @type {import('next').NextConfig} */

const { PHASE_DEVELOPMENT_SERVER } = require("next/constants");

const showDevTools = /^(1|true|yes|on)$/i.test(
  (process.env.NEXT_PUBLIC_SHOW_DEV_TOOLS || "").trim(),
);

/** @param {string} phase */
const createNextConfig = (phase) => ({
  reactStrictMode: true,
  // Development and production builds must never share webpack chunks or an
  // RSC client manifest. `next build` always keeps the standard `.next` path.
  distDir: phase === PHASE_DEVELOPMENT_SERVER ? ".next-dev" : ".next",

  // Keep recently visited route bundles warm in development. The application
  // has several large workspaces; letting Next evict them quickly makes a
  // return navigation look like a frozen click while the route recompiles.
  onDemandEntries: {
    maxInactiveAge: 30 * 60 * 1000,
    pagesBufferLength: 12,
  },

  // Turbopack substantially reduces cold route compilation time in the
  // Docker development workspace. Keep this on stable Next.js options only.
  // A concrete root also tells Next that Turbopack is intentionally configured,
  // avoiding the misleading "Webpack is configured" development warning.
  turbopack: { root: __dirname },

  /**
   * Proxy backend routes through Next.js so the browser always talks to the same origin.
   * This avoids CORS issues and removes the need for hard-coded API base URLs.
   *
   * In docker-compose, set BACKEND_URL=http://backend:8000
   */
  async rewrites() {
    const backend = process.env.BACKEND_URL || process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
    return [
      { source: "/api/:path*", destination: `${backend}/api/:path*` },
      { source: "/auth/:path*", destination: `${backend}/auth/:path*` },
      // Optional: allow direct access to persisted outputs (debug)
      { source: "/uploads/:path*", destination: `${backend}/uploads/:path*` },
    ];
  },

  // @ts-expect-error webpack config type is not fully typed
  webpack(config, { dev, isServer }) {
    if (dev && !isServer) {
      config.infrastructureLogging = {
        level: "warn",
      };
    }

    config.resolve.alias.canvas = false;
    config.resolve.fallback = {
      ...config.resolve.fallback,
      canvas: false,
    };

    return config;
  },

  devIndicators: showDevTools ? { position: "bottom-right" } : false,
});

module.exports = createNextConfig;
