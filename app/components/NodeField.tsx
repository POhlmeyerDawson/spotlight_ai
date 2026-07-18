"use client";

/**
 * The schematic (DESIGN.md §7). 3D points, perspective-projected, drawn flat.
 * Depth reads through scale and alpha, never shading — this is line art, not a
 * lit scene, which is why it is canvas 2D and not three.js.
 *
 * ---------------------------------------------------------------------------
 * HONESTY CONSTRAINT (§1.1) — discharged as case 2, "bound to something true".
 *
 * Every node here is an actual company row in the screen. Its RADIUS is that
 * company's real evidence-event count and its MARKER STATE is its real gate
 * outcome. Cluster membership is its real sector. Only the within-cluster jitter
 * is generated, from a seeded PRNG so the diagram is identical across reloads.
 *
 * What is NOT claimed: the seeded archetype companies are fictional demo records,
 * and no node is ever labelled with a person's name. The captions at every call
 * site say both of those things out loud. If there is no data, the caller renders
 * the empty state — never a decorative constellation captioned as if it were the
 * graph.
 * ---------------------------------------------------------------------------
 */

import { useEffect, useRef } from "react";

export interface SchematicNode {
  id: string;
  /** Real: number of contributing evidence events. Drives marker radius. */
  weight: number;
  /** Real: gate outcome. Drives filled vs hollow. */
  emphasis: "high" | "mid" | "low";
  /** Real: sector — decides cluster membership. */
  group: string;
}

/** mulberry32 — seeded, so the constellation never reshuffles on refresh (§7.2). */
function prng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function resolve(el: Element, name: string): string {
  // §7.5 — canvas 2D does not resolve var(); resolve once per mount.
  return getComputedStyle(el).getPropertyValue(name).trim() || "#000";
}

const FOV = 900;

export default function NodeField({
  nodes,
  marker = "dot",
  edgeStyle = "straight",
  neighbours = 2,
  seed = 20260718,
  className = "",
}: {
  nodes: SchematicNode[];
  /** Vary at least three of marker / edges / density / neighbours / ground (§7.3). */
  marker?: "dot" | "square";
  edgeStyle?: "straight" | "arc";
  neighbours?: 2 | 3;
  seed?: number;
  className?: string;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap || nodes.length === 0) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const accent = resolve(wrap, "--accent");
    const figure = resolve(wrap, "--figure");
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    // --- placement: cluster by real sector, never scatter uniformly (§7.2) ---
    const groups = Array.from(new Set(nodes.map((n) => n.group)));
    const rand = prng(seed);
    const centres = groups.map(() => ({
      x: (rand() - 0.5) * 300,
      y: (rand() - 0.5) * 240,
      z: (rand() - 0.5) * 300,
    }));

    const pts = nodes.map((n) => {
      const c = centres[groups.indexOf(n.group)];
      return {
        ...n,
        x: c.x + (rand() - 0.5) * 110,
        y: c.y + (rand() - 0.5) * 110,
        z: c.z + (rand() - 0.5) * 110,
      };
    });

    // --- edges: k-nearest, deduped by sorted index pair ---
    const edges: [number, number][] = [];
    const seen = new Set<string>();
    pts.forEach((p, i) => {
      const near = pts
        .map((q, j) => ({ j, d: Math.hypot(p.x - q.x, p.y - q.y, p.z - q.z) }))
        .filter((o) => o.j !== i)
        .sort((a, b) => a.d - b.d)
        .slice(0, neighbours);
      near.forEach(({ j }) => {
        const key = i < j ? `${i}-${j}` : `${j}-${i}`;
        if (seen.has(key)) return;
        seen.add(key);
        edges.push([i, j]);
      });
    });

    let w = 0;
    let h = 0;
    const resize = () => {
      const r = wrap.getBoundingClientRect();
      const dpr = Math.min(2, window.devicePixelRatio || 1); // cap DPR at 2 (§8.6)
      w = r.width;
      h = r.height;
      canvas.width = Math.max(1, Math.round(w * dpr));
      canvas.height = Math.max(1, Math.round(h * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(wrap);

    // --- draw-on, then HOLD STILL apart from slow ambient rotation (§7.4) ---
    let visible = false;
    let revealStart = 0;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && !visible) {
          visible = true;
          revealStart = performance.now();
        } else if (!entries[0].isIntersecting) {
          visible = false;
        }
      },
      { rootMargin: "80px" },
    );
    io.observe(wrap);

    const expoOut = (t: number) => (t >= 1 ? 1 : 1 - Math.pow(2, -10 * t));

    let raf = 0;
    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      if (!visible || w < 2) return;

      const elapsed = (now - revealStart) / 1000;
      const edgeP = reduced ? 1 : expoOut(Math.min(1, elapsed / 1.2));
      const nodeP = reduced ? 1 : expoOut(Math.min(1, Math.max(0, elapsed - 0.35) / 1.2));

      const rot = reduced ? 0.4 : now / 26000;
      const fit = Math.min(w, h) / 330;

      ctx.clearRect(0, 0, w, h);

      const proj = pts.map((p) => {
        const cos = Math.cos(rot);
        const sin = Math.sin(rot);
        const xr = p.x * cos - p.z * sin;
        const zr = p.x * sin + p.z * cos;
        const s = FOV / (FOV + zr);
        return {
          ...p,
          sx: w / 2 + xr * s * fit,
          sy: h / 2 + p.y * s * fit,
          s,
        };
      });

      // edges first
      ctx.strokeStyle = accent;
      ctx.lineWidth = 1;
      const shown = Math.ceil(edges.length * edgeP);
      for (let k = 0; k < shown; k++) {
        const [i, j] = edges[k];
        const a = proj[i];
        const b = proj[j];
        ctx.globalAlpha = 0.46 * Math.min(a.s, b.s);
        ctx.beginPath();
        ctx.moveTo(a.sx, a.sy);
        if (edgeStyle === "arc") {
          const mx = (a.sx + b.sx) / 2;
          const my = (a.sy + b.sy) / 2;
          const dx = b.sx - a.sx;
          const dy = b.sy - a.sy;
          ctx.quadraticCurveTo(mx - dy * 0.22, my + dx * 0.22, b.sx, b.sy);
        } else {
          ctx.lineTo(b.sx, b.sy);
        }
        ctx.stroke();
      }

      // then nodes
      const shownN = Math.ceil(proj.length * nodeP);
      for (let k = 0; k < shownN; k++) {
        const p = proj[k];
        // radius carries the REAL evidence count; depth carries scale + alpha only
        const r = (4.5 + Math.min(8, p.weight) * 1.5) * p.s;
        ctx.globalAlpha = Math.min(1, 0.42 + p.s * 0.6);
        const filled = p.emphasis === "high";
        ctx.fillStyle = accent;
        ctx.strokeStyle = p.emphasis === "low" ? figure : accent;
        ctx.lineWidth = 1.5;

        ctx.beginPath();
        if (marker === "square") {
          ctx.rect(p.sx - r, p.sy - r, r * 2, r * 2);
        } else {
          ctx.arc(p.sx, p.sy, r, 0, Math.PI * 2);
        }
        if (filled) {
          ctx.fill();
          ctx.globalAlpha = Math.min(1, 0.6 + p.s * 0.4);
          ctx.stroke();
        } else {
          ctx.stroke();
        }
      }
      ctx.globalAlpha = 1;
    };

    raf = requestAnimationFrame(frame);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      io.disconnect();
    };
  }, [nodes, marker, edgeStyle, neighbours, seed]);

  return (
    <div ref={wrapRef} className={`relative h-full w-full ${className}`} aria-hidden>
      <canvas ref={canvasRef} className="canvas-fill" />
    </div>
  );
}
