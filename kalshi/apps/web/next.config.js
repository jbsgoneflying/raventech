/** @type {import('next').NextConfig} */
const nextConfig = {
  basePath: "/flow-monitor",
  transpilePackages: ["@kalshi-monitor/shared"],
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:3100"}/api/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
