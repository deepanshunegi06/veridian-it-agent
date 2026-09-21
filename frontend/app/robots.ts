import type { MetadataRoute } from "next";

/* Internal tool behind auth: tell every crawler to stay out entirely. */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      disallow: "/",
    },
  };
}
