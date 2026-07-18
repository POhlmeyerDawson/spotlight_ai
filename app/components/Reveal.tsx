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
    const io = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) {
          setShown(true);
          io.disconnect();
        }
      },
      { rootMargin: "-8% 0px -8% 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
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
