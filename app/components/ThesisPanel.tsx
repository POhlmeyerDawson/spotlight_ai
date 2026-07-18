"use client";

/**
 * Thesis config — config, not code. One JSON blob, read from GET /thesis and written
 * back to POST /thesis. This opens the demo, so it has to be editable live and it has
 * to keep the edit even when the write endpoint is not there yet.
 */

import { useState } from "react";
import { putThesis, type Result } from "@/lib/api";
import type { Thesis } from "@/lib/types";

const SECTOR_OPTIONS = [
  "Developer Infrastructure",
  "AI Systems",
  "Data Tooling",
  "Security",
  "Fintech Infrastructure",
  "Bio Tooling",
];
const STAGE_OPTIONS = ["Pre-seed", "Seed", "Series A"];
const GEO_OPTIONS = [
  "North America",
  "Europe",
  "India",
  "Southeast Asia",
  "Latin America",
  "Africa",
];

function Chips({
  label,
  options,
  selected,
  onToggle,
}: {
  label: string;
  options: string[];
  selected: string[];
  onToggle: (v: string) => void;
}) {
  return (
    <fieldset>
      <legend className="meta text-[color:var(--muted)]">
        {label}
      </legend>
      <div className="mt-2 flex flex-wrap gap-2">
        {options.map((o) => {
          const on = selected.includes(o);
          return (
            <button
              key={o}
              type="button"
              onClick={() => onToggle(o)}
              aria-pressed={on}
              className={`border px-3 py-1.5 text-[13px] transition ${
                on
                  ? "border-[var(--accent)] bg-[color-mix(in_oklab,var(--accent)_20%,transparent)] text-[color:var(--figure)]"
                  : "border-[color:var(--rule)] bg-[color:var(--ink-09)] text-[color:var(--muted)] hover:border-[color:var(--figure)] hover:text-[color:var(--muted)]"
              }`}
            >
              {on ? "✓ " : "+ "}
              {o}
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}

const fmtMoney = (n: number) =>
  n >= 1_000_000 ? `$${(n / 1_000_000).toFixed(2).replace(/\.00$/, "")}M` : `$${(n / 1000).toFixed(0)}K`;

export default function ThesisPanel({
  initial,
  onChange,
}: {
  initial: Thesis;
  onChange?: (t: Thesis) => void;
}) {
  const [t, setT] = useState<Thesis>(initial);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<Result<Thesis> | null>(null);
  const [open, setOpen] = useState(true);

  const update = (patch: Partial<Thesis>) => {
    const next = { ...t, ...patch };
    setT(next);
    setSaved(null);
    onChange?.(next);
  };

  const toggle = (key: "sectors" | "stages" | "geos", v: string) =>
    update({
      [key]: t[key].includes(v) ? t[key].filter((x) => x !== v) : [...t[key], v],
    } as Partial<Thesis>);

  const save = async () => {
    setSaving(true);
    try {
      setSaved(await putThesis(t));
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="border border-[color:var(--rule)] bg-[color:var(--ground)]">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[color:var(--rule)] px-5 py-3">
        <div>
          <h2 className="meta text-[color:var(--figure)]">
            Investment thesis
          </h2>
          <p className="caption mt-0.5 max-w-none text-[color:var(--muted)]">
            Config, not code. Edited here, applied to the screen below.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {saved && (
            <span
              className="text-[13px]"
              style={{ color: saved.source === "live" ? "var(--accent)" : "var(--figure)" }}
            >
              {saved.source === "live" ? "✓ Saved to backend" : "◍ Kept locally (API down)"}
            </span>
          )}
          <button
            type="button"
            onClick={save}
            disabled={saving}
            className="border border-[var(--accent)] bg-[color-mix(in_oklab,var(--accent)_18%,transparent)] px-4 py-1.5 text-[14px] font-medium text-[color:var(--figure)] transition disabled:opacity-60"
          >
            {saving ? "Saving…" : "Save thesis"}
          </button>
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            className="border border-[color:var(--rule)] px-3 py-1.5 text-[14px] text-[color:var(--muted)] transition hover:bg-[color:var(--ink-09)]"
            aria-expanded={open}
          >
            {open ? "Collapse ▴" : "Expand ▾"}
          </button>
        </div>
      </header>

      {open && (
        <div className="grid gap-6 px-5 py-4 lg:grid-cols-[1fr_1fr_320px]">
          <div className="space-y-5">
            <Chips
              label="Sectors"
              options={SECTOR_OPTIONS}
              selected={t.sectors}
              onToggle={(v) => toggle("sectors", v)}
            />
            <Chips
              label="Stage"
              options={STAGE_OPTIONS}
              selected={t.stages}
              onToggle={(v) => toggle("stages", v)}
            />
          </div>

          <div className="space-y-5">
            <Chips
              label="Geography"
              options={GEO_OPTIONS}
              selected={t.geos}
              onToggle={(v) => toggle("geos", v)}
            />
            <div>
              <span className="meta text-[color:var(--muted)]">
                Check size
              </span>
              <div className="mt-2 flex items-center gap-3">
                <label className="flex-1">
                  <span className="sr-only">Minimum check size</span>
                  <input
                    type="number"
                    step={50_000}
                    min={0}
                    value={t.check_size_min}
                    onChange={(e) => update({ check_size_min: Number(e.target.value) })}
                    className="mono w-full border border-[color:var(--rule)] bg-[color:var(--ink-09)] px-3 py-2 text-[15px] text-[color:var(--figure)]"
                  />
                </label>
                <span className="text-[color:var(--muted)]">→</span>
                <label className="flex-1">
                  <span className="sr-only">Maximum check size</span>
                  <input
                    type="number"
                    step={50_000}
                    min={0}
                    value={t.check_size_max}
                    onChange={(e) => update({ check_size_max: Number(e.target.value) })}
                    className="mono w-full border border-[color:var(--rule)] bg-[color:var(--ink-09)] px-3 py-2 text-[15px] text-[color:var(--figure)]"
                  />
                </label>
              </div>
              <p className="mono mt-1.5 text-[14px] text-[color:var(--muted)]">
                {fmtMoney(t.check_size_min)} – {fmtMoney(t.check_size_max)}
              </p>
            </div>
          </div>

          <div className="space-y-5">
            <div>
              <label
                htmlFor="risk"
                className="meta text-[color:var(--muted)]"
              >
                Risk appetite
              </label>
              <div className="mono mt-1 text-[40px] leading-none font-medium text-[color:var(--figure)]">
                {t.risk_appetite}
              </div>
              <input
                id="risk"
                type="range"
                min={0}
                max={100}
                value={t.risk_appetite}
                onChange={(e) => update({ risk_appetite: Number(e.target.value) })}
                className="mt-2 w-full accent-[var(--accent)]"
              />
              <div className="flex justify-between text-[12px] text-[color:var(--muted)]">
                <span>evidence-heavy</span>
                <span>conviction-heavy</span>
              </div>
            </div>

            <div>
              <label
                htmlFor="notes"
                className="meta text-[color:var(--muted)]"
              >
                Standing note
              </label>
              <textarea
                id="notes"
                rows={4}
                value={t.notes}
                onChange={(e) => update({ notes: e.target.value })}
                className="mt-2 w-full border border-[color:var(--rule)] bg-[color:var(--ink-09)] px-3 py-2 text-[14px] leading-[1.55] text-[color:var(--figure)]"
              />
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
