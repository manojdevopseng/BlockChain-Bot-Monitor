/** @type {import('next').NextConfig} */
const API = process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000";

const nextConfig = {
  reactStrictMode: true,
  // Set only for a preview build served under a prefix; empty for the real
  // deployment, which owns the root.
  basePath: process.env.NEXT_PUBLIC_BASE_PATH || undefined,
  // Proxy /api and /ws to the FastAPI backend in dev so the frontend uses
  // same-origin URLs (nginx does the same in production).
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API}/api/:path*` },
      // The realtime socket takes the same road as the API, so a build that
      // proxies one proxies both — otherwise the dashboard sits on "Offline"
      // and falls back to polling for no reason.
      { source: "/ws", destination: `${API}/ws` },
    ];
  },
};

module.exports = nextConfig;
