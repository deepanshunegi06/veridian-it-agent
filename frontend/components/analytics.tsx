"use client";

/* Google Analytics loader. Renders nothing until NEXT_PUBLIC_GA_ID is set
 * (e.g. G-XXXXXXXXXX in the Vercel project env), so local builds and
 * deploys without analytics stay clean. Consent handling is intentionally
 * left out: this is an internal workspace, not a public marketing site. */

import Script from "next/script";

const GA_ID = process.env.NEXT_PUBLIC_GA_ID;

export function Analytics() {
  if (!GA_ID) return null;
  return (
    <>
      <Script
        src={`https://www.googletagmanager.com/gtag/js?id=${GA_ID}`}
        strategy="afterInteractive"
      />
      <Script id="ga-init" strategy="afterInteractive">
        {`window.dataLayer = window.dataLayer || [];
function gtag(){dataLayer.push(arguments);}
gtag('js', new Date());
gtag('config', '${GA_ID}');`}
      </Script>
    </>
  );
}
