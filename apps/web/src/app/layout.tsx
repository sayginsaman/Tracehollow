import "@fontsource-variable/ibm-plex-sans/wght.css";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import "./globals.css";

import type { Metadata, Viewport } from "next";

import { THEME_SCRIPT } from "@/lib/theme";

export const metadata: Metadata = {
  title: { default: "Tracehollow", template: "%s · Tracehollow" },
  description: "Local-first investigation workspace",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f8f7f4" },
    { media: "(prefers-color-scheme: dark)", color: "#141210" },
  ],
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    // The theme script sets data-theme before hydration, so the attribute may differ from the server render.
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="min-h-dvh">
        <a
          href="#main"
          className="sr-only rounded-md bg-surface px-3 py-2 font-medium text-ink shadow-overlay focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-(--z-popover)"
        >
          Skip to content
        </a>
        {children}
      </body>
    </html>
  );
}
