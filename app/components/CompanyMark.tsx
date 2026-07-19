"use client";

/**
 * The mark beside a company name.
 *
 * THE RULE THIS COMPONENT EXISTS TO ENFORCE: a real logo if the record carries one,
 * and a typographic monogram if it does not. Never a guess.
 *
 * The tempting third option — derive a domain from the company name and pull
 * `https://<guess>/favicon.ico` or a favicon service — is not implemented and must not
 * be. It succeeds often enough to look like it works and fails silently by attaching a
 * stranger's brand to a company in an investor's shortlist. That is a fabrication with
 * someone else's trademark on it, and it would be the only unsourced assertion on a
 * page whose whole argument is that its assertions are sourced.
 *
 * `lib/standout.ts::logoUrl` is the single point where a real logo would enter. It
 * returns null for every company today because NO COMPANY ROUTE SERVES A LOGO FIELD.
 * When one exists, this component renders it and nothing else here changes.
 *
 * The monogram is drawn in the display serif inside a ruled square — the same
 * furniture idiom as the rest of the document (DESIGN.md §4.3), not a coloured avatar
 * chip. It is legible as type, which is the point: it reads as a placeholder standing
 * in for a mark, rather than as a mark.
 */

import { useState } from "react";

/** Up to two initials from the company name. Punctuation and stopwords dropped. */
export function monogram(name: string): string {
  const words = name
    .replace(/[^\p{L}\p{N}\s]/gu, " ")
    .split(/\s+/)
    .filter((w) => w.length > 0 && !/^(the|and|of|labs?|inc|ltd|co)$/i.test(w));
  if (!words.length) return name.slice(0, 2).toUpperCase() || "—";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

export default function CompanyMark({
  name,
  logo,
  size = 44,
}: {
  name: string;
  /** A real, stored logo URL. Null/undefined renders the monogram. */
  logo?: string | null;
  size?: number;
}) {
  // A logo that 404s must fall back to the monogram rather than to a broken image
  // icon, which reads as a bug rather than as an absence.
  const [broken, setBroken] = useState(false);
  const showLogo = Boolean(logo) && !broken;

  return (
    <span
      aria-hidden
      title={
        showLogo
          ? `${name} — logo as stored on the company record`
          : `No logo is stored for ${name}. This is a monogram of the company name, not a logo — nothing was fetched or guessed.`
      }
      className="inline-flex shrink-0 items-center justify-center overflow-hidden border border-[color:var(--rule)] bg-[color:var(--ink-09)]"
      style={{ width: size, height: size }}
    >
      {showLogo ? (
        // An arbitrary stored third-party URL cannot go through next/image, whose
        // remote loader requires the host to be on a configured allowlist.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={logo as string}
          alt=""
          width={size}
          height={size}
          onError={() => setBroken(true)}
          className="h-full w-full object-contain"
        />
      ) : (
        <span
          className="font-[family-name:var(--font-instrument-serif)] leading-none text-[color:var(--muted)]"
          style={{ fontSize: size * 0.44 }}
        >
          {monogram(name)}
        </span>
      )}
    </span>
  );
}
