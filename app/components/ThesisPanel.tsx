"use client";

/**
 * The STANDING thesis — the fund's persistent policy, not a view filter.
 *
 * This is deliberately not on `/pipeline` any more. The pipeline has one control, a
 * natural-language box, and that box NARROWS THE VIEW: it dims rows that are already
 * screened in. The thesis does something categorically stronger — `core/thesis.in_scope`
 * EXCLUDES companies from the pipeline entirely, so a sector removed here is a sector
 * the fund stops seeing. Stacking the two controls on one page invited a VC to type a
 * one-off question and permanently rewrite the fund's mandate by accident.
 *
 * Two further things this panel now gets right:
 *
 *   1. There is NO fixed sector list. The old six options ("Developer Infrastructure",
 *      "AI Systems", …) capped what a fund could even express: consumer social,
 *      logistics, defense and climate were unsayable. Sectors, stages and geographies
 *      are all free text.
 *   2. It writes the shape the backend actually consumes. `lib/api.ts` translates to
 *      the nested document `core/thesis.py` reads, and merges rather than replaces, so
 *      fields this UI does not model survive the save.
 */

import { useState } from "react";
import { putThesis, type Result } from "@/lib/api";
import type { Thesis } from "@/lib/types";

/** Suggestions, explicitly not a vocabulary — the field accepts anything. */
const STAGE_HINTS = ["pre-seed", "seed", "series-a"];

function TokenField({
  label,
  hint,
  placeholder,
  values,
  onChange,
}: {
  label: string;
  hint?: string;
  placeholder: string;
  values: string[];
  onChange: (next: string[]) => void;
}) {
  const [draft, setDraft] = useState("");

  const add = (raw: string) => {
    const v = raw.trim();
    if (!v || values.some((x) => x.toLowerCase() === v.toLowerCase())) return;
    onChange([...values, v]);
  };

  return (
    <fieldset>
      <legend className="meta text-[color:var(--muted)]">{label}</legend>
      {hint && (
        <p className="caption mt-0.5 max-w-none text-[color:var(--muted)]">{hint}</p>
      )}
      <div className="mt-2 flex flex-wrap gap-2">
        {values.map((v) => (
          <span
            key={v}
            className="flex items-center gap-2 border border-[var(--accent)] bg-[color-mix(in_oklab,var(--accent)_20%,transparent)] px-3 py-1.5 text-[13px] text-[color:var(--figure)]"
          >
            {v}
            <button
              type="button"
              onClick={() => onChange(values.filter((x) => x !== v))}
              aria-label={`Remove ${v} from ${label}`}
              className="text-[color:var(--muted)] hover:text-[color:var(--figure)]"
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <div className="mt-2 flex gap-2">
        <input
          value={draft}
          placeholder={placeholder}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              add(draft);
              setDraft("");
            }
          }}
          className="min-w-0 flex-1 border border-[color:var(--rule)] bg-[color:var(--ink-09)] px-3 py-2 text-[14px] text-[color:var(--figure)]"
        />
        <button
          type="button"
          onClick={() => {
            add(draft);
            setDraft("");
          }}
          disabled={!draft.trim()}
          className="border border-[color:var(--rule)] px-3 py-2 text-[13px] text-[color:var(--muted)] disabled:opacity-50"
        >
          ADD
        </button>
      </div>
    </fieldset>
  );
}

const fmtMoney = (n: number) =>
  n >= 1_000_000
    ? `$${(n / 1_000_000).toFixed(2).replace(/\.00$/, "")}M`
    : `$${(n / 1000).toFixed(0)}K`;

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

  const update = (patch: Partial<Thesis>) => {
    const next = { ...t, ...patch };
    setT(next);
    setSaved(null);
    onChange?.(next);
  };

  const save = async () => {
    setSaving(true);
    try {
      const r = await putThesis(t);
      setSaved(r);
      if (r.source === "live") setT(r.data);
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="border border-[color:var(--rule)] bg-[color:var(--ground)]">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[color:var(--rule)] px-5 py-3">
        <div>
          <h2 className="meta text-[color:var(--figure)]">Standing thesis</h2>
          <p className="caption mt-0.5 max-w-none text-[color:var(--muted)]">
            Config, not code — and stronger than a filter. Sectors, stages and
            geographies set here <strong>exclude companies from the pipeline
            entirely</strong>; they are never merely dimmed.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {saved && (
            <span
              className="text-[13px]"
              style={{ color: saved.source === "live" ? "var(--accent)" : "var(--figure)" }}
            >
              {saved.source === "live"
                ? "✓ Saved to backend"
                : `◍ Kept locally (${saved.note ?? "API down"})`}
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
        </div>
      </header>

      <div className="grid gap-6 px-5 py-4 lg:grid-cols-[1fr_1fr_320px]">
        <div className="space-y-5">
          <TokenField
            label="Sectors"
            hint="Any industry, typed freely. There is no fixed list — consumer social, logistics, defense and climate hardware are all valid."
            placeholder="e.g. climate hardware"
            values={t.sectors}
            onChange={(sectors) => update({ sectors })}
          />
          <TokenField
            label="Stage"
            hint={`Suggestions: ${STAGE_HINTS.join(", ")}. Anything else is accepted too.`}
            placeholder="e.g. seed"
            values={t.stages}
            onChange={(stages) => update({ stages })}
          />
        </div>

        <div className="space-y-5">
          <TokenField
            label="Geography"
            hint="Empty means unrestricted, which is the shipped default: geographic filters are the cheapest way to systematically miss a Type 6 founder."
            placeholder="e.g. Southeast Asia"
            values={t.geos}
            onChange={(geos) => update({ geos })}
          />
          <div>
            <span className="meta text-[color:var(--muted)]">Check size</span>
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
            <label htmlFor="risk" className="meta text-[color:var(--muted)]">
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
            <p className="caption mt-2 max-w-none text-[color:var(--muted)]">
              Moves the evidence bar, never the score. A bolder fund proceeds on thinner
              evidence; the same founder does not become more capable.
            </p>
          </div>

          <div>
            <label htmlFor="notes" className="meta text-[color:var(--muted)]">
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
    </section>
  );
}
