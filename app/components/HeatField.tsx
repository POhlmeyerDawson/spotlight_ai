"use client";

/**
 * The posterised heat field (DESIGN.md §6).
 *
 * A continuous scalar field on a fixed 9px lattice, quantised into hard bands.
 * One mechanism, two visible states: at a low threshold every cell paints
 * (mosaic); as the threshold rises almost everything decays and the isolated
 * survivors read as structure (schematic).
 *
 * Here that is not decoration — it IS the product thesis. A dense field of
 * thousands of builders resolves to the few who survive the evidence threshold.
 * Plate 06 re-runs plate 01's generator at a high threshold: the closing image is
 * literally what survived the opening one.
 *
 * The four rules that decide whether it looks right (§6.3):
 *   1. hard banding, never interpolated
 *   2. DIFFUSE stays at 0.045 — the most sensitive constant
 *   3. STATIC per-cell grain via a stable hash, or the edges shimmer
 *   4. cell size fixed in SCREEN pixels, never scaled to viewport
 */

import { useEffect, useRef } from "react";

const CELL = 9; // px, fixed in SCREEN space, never scaled to viewport
const BRUSH = 10; // pointer heat radius, in cells
const RELAX = 0.055; // how fast the field chases its target
const DIFFUSE = 0.045; // neighbour bleed — do not raise, it fuses the bands
const COOL = 0.94; // pointer heat decay per frame
const FREQ = 0.052; // noise frequency
const GRAIN = 0.17; // static per-cell dither

/** Cool → hot. Below the first cut the cell is UNPAINTED and the ground shows. */
const BAND_CUTS = [0.3, 0.46, 0.62, 0.78];
const BAND_VARS = ["--ink", "--cobalt", "--sky", "--signal"] as const;

type RGB = [number, number, number];

function hexToRgb(hex: string): RGB {
  const h = hex.trim().replace("#", "");
  const full = h.length === 3 ? h.replace(/./g, (c) => c + c) : h;
  const n = parseInt(full, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/**
 * §7.5 — canvas 2D does NOT resolve `var(--sky)`; it silently paints black with
 * no console warning. Resolve once per mount, never per frame.
 */
function resolveVars(el: Element, names: readonly string[]): RGB[] {
  const cs = getComputedStyle(el);
  return names.map((n) => hexToRgb(cs.getPropertyValue(n) || "#000000"));
}

/** Stable integer hash → [0,1). Static per cell, so the grain never shimmers. */
function hash2(x: number, y: number): number {
  let h = x * 374761393 + y * 668265263;
  h = (h ^ (h >> 13)) * 1274126177;
  return ((h ^ (h >> 16)) >>> 0) / 4294967296;
}

const smooth = (t: number) => t * t * (3 - 2 * t);

/** Value noise, bilinear + smoothstep. Cheap and stable. */
function vnoise(x: number, y: number): number {
  const ix = Math.floor(x);
  const iy = Math.floor(y);
  const fx = smooth(x - ix);
  const fy = smooth(y - iy);
  const a = hash2(ix, iy);
  const b = hash2(ix + 1, iy);
  const c = hash2(ix, iy + 1);
  const d = hash2(ix + 1, iy + 1);
  return a * (1 - fx) * (1 - fy) + b * fx * (1 - fy) + c * (1 - fx) * fy + d * fx * fy;
}

/**
 * §6.4 — diagonal falloff, mass upper-left, dissolving down and right.
 *
 * Coefficients retuned from the doc's reference (0.42/0.62, ×1.9) for this
 * layout: the plate's visual row is wide and short, so `v` climbs across very
 * little height while `u` runs the full width. Weighting `u` harder is what makes
 * the mass decay ACROSS the plate instead of painting the whole upper half, and
 * the lower amplitude keeps --signal down to a hot core rather than a region —
 * the palette rations it to ~2% of pixels. §6.4 asks for exactly this tuning.
 */
const ramp = (u: number, v: number) =>
  Math.pow(Math.max(0, 1 - (u * 0.62 + v * 0.42)), 1.15) * 1.25;

export default function HeatField({
  /** Base threshold lift. 0 = mosaic (plate 01). ~0.34 = starved schematic (plate 06). */
  starve = 0,
  /** When true, scroll progress across this element lifts the threshold further (§8.4). */
  scrollStarve = false,
  className = "",
}: {
  starve?: number;
  scrollStarve?: boolean;
  className?: string;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  // §8.4 — scroll progress is written to a REF, never React state. Re-rendering a
  // canvas host at 60fps is pure waste.
  const scrollRef = useRef(0);
  const scrollTargetRef = useRef(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;

    const ctx = canvas.getContext("2d", { alpha: true });
    if (!ctx) return;

    const colors = resolveVars(wrap, BAND_VARS);
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const finePointer = window.matchMedia("(pointer: fine)").matches;

    let gw = 0;
    let gh = 0;
    let val: Float32Array = new Float32Array(0);
    let tgt: Float32Array = new Float32Array(0);
    let heat: Float32Array = new Float32Array(0);
    let grain: Float32Array = new Float32Array(0);
    let img: ImageData | null = null;

    const resize = () => {
      const r = wrap.getBoundingClientRect();
      if (r.width < 2 || r.height < 2) return;
      // Cell size fixed in screen px: a larger viewport simply holds more cells.
      const nw = Math.max(2, Math.ceil(r.width / CELL));
      const nh = Math.max(2, Math.ceil(r.height / CELL));
      if (nw === gw && nh === gh) return;
      gw = nw;
      gh = nh;
      // §6.5 — the backing store IS the grid. CSS upscales it with
      // image-rendering: pixelated, which is far cheaper than drawing rects
      // and is pixel-exact by construction.
      canvas.width = gw;
      canvas.height = gh;
      val = new Float32Array(gw * gh);
      tgt = new Float32Array(gw * gh);
      heat = new Float32Array(gw * gh);
      grain = new Float32Array(gw * gh);
      for (let y = 0; y < gh; y++) {
        for (let x = 0; x < gw; x++) grain[y * gw + x] = hash2(x, y) - 0.5;
      }
      img = ctx.createImageData(gw, gh);
    };

    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(wrap);

    // --- pointer as a heat source (§6.6). Gated on (pointer: fine). ---
    let px = -999;
    let py = -999;
    const onMove = (e: PointerEvent) => {
      const r = wrap.getBoundingClientRect();
      px = (e.clientX - r.left) / CELL;
      py = (e.clientY - r.top) / CELL;
    };
    const onLeave = () => {
      px = -999;
      py = -999;
    };
    if (finePointer && !reduced) {
      wrap.addEventListener("pointermove", onMove);
      wrap.addEventListener("pointerleave", onLeave);
    }

    // --- scroll-driven threshold (§8.4), with scrub:1-style catch-up ---
    const onScroll = () => {
      if (!scrollStarve) return;
      const r = wrap.getBoundingClientRect();
      const total = r.height + window.innerHeight;
      const p = 1 - (r.bottom + window.innerHeight - total) / Math.max(1, r.height);
      scrollTargetRef.current = Math.min(1, Math.max(0, p)) * 0.55;
    };
    if (scrollStarve) {
      window.addEventListener("scroll", onScroll, { passive: true });
      onScroll();
    }

    // --- IntersectionObserver gate: an off-screen plate costs zero (§8.6) ---
    let visible = false;
    const io = new IntersectionObserver(
      (entries) => {
        visible = entries[0].isIntersecting;
      },
      { rootMargin: "120px" },
    );
    io.observe(wrap);

    let raf = 0;
    let t = 0;
    // RELAX is a slow chase; without a warm start the first ~40 frames render an
    // almost-empty field, which is what the viewer sees on a fresh page load.
    let warmed = false;

    const frame = () => {
      raf = requestAnimationFrame(frame);
      if (!visible || !img || gw === 0) return;

      // scrub: 1 — a one-second catch-up is what produces weight.
      scrollRef.current += (scrollTargetRef.current - scrollRef.current) * 0.06;
      // Snap micro-velocity to exact zero or the page never feels at rest (§8.6).
      if (Math.abs(scrollTargetRef.current - scrollRef.current) < 0.0005) {
        scrollRef.current = scrollTargetRef.current;
      }

      if (!reduced) t += 0.006;

      // 1. target field: shaped noise
      for (let y = 0; y < gh; y++) {
        const v = y / gh;
        for (let x = 0; x < gw; x++) {
          const u = x / gw;
          const n =
            vnoise(x * FREQ + t * 0.9, y * FREQ) * 0.6 +
            vnoise(x * FREQ * 2.1 - t * 0.55, y * FREQ * 2.1 + t * 0.25) * 0.4;
          tgt[y * gw + x] = ramp(u, v) * (0.45 + 0.72 * n);
        }
      }

      // 2. relax toward target (seeded from it on the first painted frame)
      if (!warmed) {
        val.set(tgt);
        warmed = true;
      } else {
        for (let i = 0; i < val.length; i++) val[i] += (tgt[i] - val[i]) * RELAX;
      }

      // 3. neighbour bleed — kept low, or the bands fuse into a weather map
      for (let y = 1; y < gh - 1; y++) {
        for (let x = 1; x < gw - 1; x++) {
          const i = y * gw + x;
          const avg =
            (val[i - 1] + val[i + 1] + val[i - gw] + val[i + gw]) * 0.25 - val[i];
          val[i] += avg * DIFFUSE;
        }
      }

      // 4. pointer heat, radial falloff squared, max-accumulated, cooling
      for (let i = 0; i < heat.length; i++) heat[i] *= COOL;
      if (px > -900) {
        const x0 = Math.max(0, Math.floor(px - BRUSH));
        const x1 = Math.min(gw - 1, Math.ceil(px + BRUSH));
        const y0 = Math.max(0, Math.floor(py - BRUSH));
        const y1 = Math.min(gh - 1, Math.ceil(py + BRUSH));
        for (let y = y0; y <= y1; y++) {
          for (let x = x0; x <= x1; x++) {
            const d = Math.hypot(x - px, y - py) / BRUSH;
            if (d >= 1) continue;
            const q = (1 - d) * (1 - d);
            const i = y * gw + x;
            if (q > heat[i]) heat[i] = q;
          }
        }
      }

      // 5. band it — HARD, never interpolated. Grain is added BEFORE banding so
      //    edges break into individually surviving cells (the dissolve).
      const lift = starve + scrollRef.current;
      const d = img.data;
      for (let i = 0; i < val.length; i++) {
        const v = val[i] + heat[i] * 0.85 + grain[i] * GRAIN - lift;
        let band = -1;
        for (let b = BAND_CUTS.length - 1; b >= 0; b--) {
          if (v >= BAND_CUTS[b]) {
            band = b;
            break;
          }
        }
        const o = i * 4;
        if (band < 0) {
          d[o + 3] = 0; // unpainted: the ground shows through
        } else {
          const c = colors[band];
          d[o] = c[0];
          d[o + 1] = c[1];
          d[o + 2] = c[2];
          d[o + 3] = 255;
        }
      }
      ctx.putImageData(img, 0, 0);
    };

    raf = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      io.disconnect();
      wrap.removeEventListener("pointermove", onMove);
      wrap.removeEventListener("pointerleave", onLeave);
      window.removeEventListener("scroll", onScroll);
    };
  }, [starve, scrollStarve]);

  return (
    <div ref={wrapRef} className={`relative h-full w-full ${className}`} aria-hidden>
      <canvas
        ref={canvasRef}
        className="canvas-fill"
        style={{ imageRendering: "pixelated" }}
      />
    </div>
  );
}
