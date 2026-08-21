import type { Metadata, Viewport } from "next";
import "./globals.css";
import { Shell } from "@/components/layout/Shell";
import { themeInitScript } from "@/lib/theme";

export const metadata: Metadata = {
  title: "SightLine — see it before the chart does",
  description: "Multichain token monitor dashboard",
  // Every size a browser or a phone asks for, from one badge. The SVG is
  // listed first so anything that can scale takes that one; the .ico carries
  // the 16 and 32 pixel versions a tab actually paints.
  icons: {
    icon: [
      { url: "/icon.svg", type: "image/svg+xml" },
      { url: "/icon-192.png", sizes: "192x192", type: "image/png" },
      { url: "/icon-512.png", sizes: "512x512", type: "image/png" },
    ],
    apple: [{ url: "/apple-touch-icon.png", sizes: "180x180" }],
  },
  manifest: "/site.webmanifest",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f8fafc" },
    { media: "(prefers-color-scheme: dark)", color: "#0a0e17" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // `dark` is the SSR default; the init script corrects it before paint.
    <html lang="en" className="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
