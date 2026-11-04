import type { Metadata, Viewport } from "next";
import { cookies, headers } from "next/headers";

import { ConfirmPassword } from "@/components/confirm-password";
import { ServiceWorker } from "@/components/service-worker";
import { WindowTheme } from "@/lib/client/theme";
import { THEME_COOKIE, THEME_SCRIPT, chosenTheme } from "@/lib/theme";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Ringkeeper", template: "%s · Ringkeeper" },
  description: "Your clients' projects, connections and approvals in one place.",
  applicationName: "Ringkeeper",
  appleWebApp: { capable: true, title: "Ringkeeper", statusBarStyle: "default" },
  icons: {
    icon: [
      { url: "/icon.svg", type: "image/svg+xml" },
      { url: "/icon-32.png", sizes: "32x32", type: "image/png" },
      { url: "/icon-16.png", sizes: "16x16", type: "image/png" },
    ],
    apple: "/apple-icon.png",
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f1f0ec" },
    { media: "(prefers-color-scheme: dark)", color: "#121211" },
  ],
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // Reading a request header makes every page dynamic, which the CSP nonce needs
  // (Next.js adds the nonce from the request's CSP header to its own scripts).
  // The desktop app's pages are exported as files instead: no request, no nonce.
  // The theme choice (lib/theme.ts) is set before the page draws: here from the
  // cookie, or for the exported desktop pages by a script that reads it.
  const desktop = process.env.NEXT_PUBLIC_RINGKEEPER_TARGET === "desktop";
  let theme: string | undefined;
  if (!desktop) {
    await headers();
    theme = chosenTheme((await cookies()).get(THEME_COOKIE)?.value);
  }
  return (
    <html lang="en" data-theme={theme} suppressHydrationWarning>
      <head>{desktop ? <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} /> : null}</head>
      <body className="min-h-dvh antialiased">
        {children}
        <ConfirmPassword />
        <ServiceWorker />
        <WindowTheme />
      </body>
    </html>
  );
}
