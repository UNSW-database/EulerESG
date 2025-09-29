/** @type {import('next').NextConfig} */

const nextConfig = {
  reactStrictMode: true,

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

  devIndicators: {
    autoPrerender: false,
  },
};

module.exports = nextConfig;
