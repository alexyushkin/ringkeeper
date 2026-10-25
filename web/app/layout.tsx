import type { Metadata, Viewport } from "next";
import { headers } from "next/headers";

import { ServiceWorker } from "@/components/service-worker";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Ringkeeper", template: "%s · Ringkeeper" },
  description: "Your clients' projects, connections and approvals in one place.",
  applicationName: "Ringkeeper",
  appleWebApp: { capable: true, title: "Ringkeeper", statusBarStyle: "default" },
  icons: { apple: "/apple-icon.png" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f7f7f5" },
    { media: "(prefers-color-scheme: dark)", color: "#121211" },
  ],
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // Reading a request header makes every page dynamic, which the CSP nonce needs
  // (Next.js adds the nonce from the request's CSP header to its own scripts).
  await headers();
  return (
    <html lang="en">
      <body className="min-h-dvh antialiased">
        {children}
        <ServiceWorker />
      </body>
    </html>
  );
}
