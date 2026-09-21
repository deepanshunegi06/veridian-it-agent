import type { NextConfig } from "next";

/* HTTPS/HSTS come free from Vercel. These are the headers Vercel does not
 * set for us: clickjacking protection for the login page, MIME sniffing
 * guard, a tight referrer policy, and no sensor access. */
const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "SAMEORIGIN" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

const nextConfig: NextConfig = {
  poweredByHeader: false,
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
