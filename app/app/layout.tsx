import type { Metadata } from "next";
import { Geist_Mono, Instrument_Serif, Inter } from "next/font/google";
import "./globals.css";

/*
 * Three faces, three jobs, no overlap (DESIGN.md §3.1).
 *   Display  — Instrument Serif 400
 *   Metadata — Geist Mono 400 (load-bearing: Swiss skeleton, not a dev-tool face)
 *   Body     — Inter
 * next/font self-hosts the WOFF2 latin subset from our own origin at build time,
 * which is what §3.1 asks for — no runtime request to Google.
 */
const instrumentSerif = Instrument_Serif({
  variable: "--font-instrument-serif",
  subsets: ["latin"],
  weight: "400",
  display: "swap",
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
  weight: ["400", "500"],
  display: "swap",
});

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "VC Brain — a dense field resolves to a sparse structure",
  description:
    "Three-axis founder screening with per-claim evidence, adversarial dissent, and a no-lookahead backtest.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      className={`${instrumentSerif.variable} ${geistMono.variable} ${inter.variable}`}
    >
      <body>{children}</body>
    </html>
  );
}
