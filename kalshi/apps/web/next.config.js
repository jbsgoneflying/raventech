/** @type {import('next').NextConfig} */
const nextConfig = {
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
