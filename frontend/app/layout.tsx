import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { AuthProvider } from "@/components/auth-provider";
import { Analytics } from "@/components/analytics";
import { Shell } from "@/components/shell";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  metadataBase: new URL("https://veridian-it-dek.vercel.app/"),
  title: {
    default: "Veridian IT Desk",
    template: "%s · Veridian IT Desk",
  },
  description:
    "Internal IT support workspace: chat with the agent, hand off to a person, resolve with citations.",
  // Internal tool behind auth: keep every page out of public indexes.
  robots: {
    index: false,
    follow: false,
  },
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} light h-full antialiased`}
      suppressHydrationWarning
      style={{ colorScheme: "light" }}
    >
      <body className="min-h-full bg-slate-50 text-slate-900" suppressHydrationWarning>
        <Analytics />
        <AuthProvider>
          <Shell>{children}</Shell>
        </AuthProvider>
      </body>
    </html>
  );
}
