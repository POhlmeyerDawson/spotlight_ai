"use client";

/**
 * Entry reveal (DESIGN.md §8.3): 24px of travel, 1.1s, expo.out. That is all.
 *
 * Motion applies to SOME layers, not all — uniform animation across every element
 * is the definitive tell of a templated site. So captions and metadata take the
 * `quiet` variant (opacity only, 0.5s, power2.out) while display type travels.
 * Siblings stagger by hand at 60ms.
 *
 * No GSAP/Lenis here: the travel, easing and stagger budget the doc specifies are
 * expressible in CSS transitions, and native scroll keeps sticky positioning and
 * keyboard paging intact — which is the reason §8.1 prefers Lenis over
 * ScrollSmoother in the first place.
 */

import { useEffect, useRef, useState, type ReactNode } from "react";

/**
 * How long to wait for the observer to say something before giving up on it.
 *
 * The reveal is an ENHANCEMENT and it must never be able to withhold the content. The
 * failure mode this guards against is total: the hidden state lives in CSS, so any
 * situation where the callback does not fire — no JS, prerendered HTML served to a
 * crawler or a link preview, an IntersectionObserver that never reports (headless and
 * offscreen renderers do this) — leaves EVERY plate on the page at opacity 0 with no
 * way back. A page that renders blank is a worse outcome than one that renders without
 * its animation, so after this long the content is shown regardless.
 */
const FAILSAFE_MS = 1500;

export default function Reveal({
  children,
  quiet = false,
  delay = 0,
  className = "",
}: {
  children: ReactNode;
  /** Captions, rules, metadata: opacity only, no travel. */
  quiet?: boolean;
  /** Hand-staggered siblings: 60ms apart. */
  delay?: number;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [shown, setShown] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const reveal = () => setShown(true);

    if (typeof IntersectionObserver === "undefined") {
      reveal();
      return;
    }

    const io = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) {
          reveal();
          io.disconnect();
        }
      },
      { rootMargin: "-8% 0px -8% 0px" },
    );
    io.observe(el);

    // The same test the observer is making, by hand. Same 8% inset, so an element
    // rescued this way animates at the point it would have anyway.
    const onScroll = () => {
      const r = el.getBoundingClientRect();
      const inset = window.innerHeight * 0.08;
      if (r.top < window.innerHeight - inset && r.bottom > inset) {
        reveal();
        window.removeEventListener("scroll", onScroll);
      }
    };

    // Installed unconditionally once the observer has had its chance, rather than only
    // when it has been caught staying silent: whether it is broken is not reliably
    // observable from here, and there is nothing to gain from being clever about it.
    // If the observer is alive it has already fired and this finds nothing left to do.
    const failsafe = setTimeout(() => {
      onScroll();
      window.addEventListener("scroll", onScroll, { passive: true });
    }, FAILSAFE_MS);

    return () => {
      clearTimeout(failsafe);
      window.removeEventListener("scroll", onScroll);
      io.disconnect();
    };
  }, []);

  return (
    <div
      ref={ref}
      className={`${quiet ? "reveal-quiet" : "reveal"} ${shown ? "is-in" : ""} ${className}`}
      style={delay ? { transitionDelay: `${delay}ms` } : undefined}
    >
      {children}
    </div>
  );
}
