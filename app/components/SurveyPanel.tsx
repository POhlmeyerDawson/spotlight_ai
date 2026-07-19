"use client";

/**
 * The twelve forced trade-offs (docs/DIFFERENTIATOR.md §2.1).
 *
 * THREE THINGS THIS COMPONENT IS NOT ALLOWED TO DO.
 *
 * 1. It does not hold the questions. Every prompt and every option string is rendered
 *    from `GET /profile/survey`. The catalog is served precisely so the questions and
 *    the signals they emit cannot drift apart; a hardcoded copy here would show a VC a
 *    question the derivation has no signals for, and it would contribute silently
 *    nothing.
 * 2. It does not render a slider. These are trade-offs, not agreement scales — the two
 *    options are genuine alternatives and are given equal visual weight, side by side.
 *    A Likert scale would measure agreeableness; the point is to measure priorities.
 * 3. It does not default an answer. An unanswered question is ABSENT, not neutral, and
 *    the API treats it that way: it lowers confidence rather than being imputed. So the
 *    progress readout counts answered questions and names the unanswered ones instead of
 *    showing a half-filled bar as though the middle were an opinion.
 */

import { useCallback, useEffect, useState } from "react";
import { getSurvey, postSurvey, type Survey } from "@/lib/vc";
import { Busy, ErrorNote, Loading, Panel, ProgressBar } from "@/components/ui";
import { TIMEOUT } from "@/lib/api";

export default function SurveyPanel({ onSaved }: { onSaved?: () => void }) {
  const [survey, setSurvey] = useState<Survey | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  /** Local answers, seeded from the server's. "" is never stored — absent is absent. */
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  /** State is written only after the await, so no effect body sets state synchronously. */
  const load = useCallback(async () => {
    const r = await getSurvey();
    if (!r.ok) {
      setLoadError(
        r.status === 401
          ? "the survey belongs to an account — sign in to answer it"
          : `GET /profile/survey: ${r.error}`,
      );
      return;
    }
    setLoadError(null);
    setSurvey(r.data);
    setAnswers(r.data.answers ?? {});
  }, []);

  useEffect(() => {
    void (async () => {
      await load();
    })();
  }, [load]);

  if (loadError) {
    return (
      <Panel title="the twelve trade-offs">
        <ErrorNote message={loadError} onRetry={() => void load()} />
      </Panel>
    );
  }
  if (!survey) return <Loading label="the survey" />;

  const answeredCount = Object.keys(answers).length;
  const unanswered = survey.questions.filter((q) => !answers[q.id]);
  const dirty = survey.questions.some((q) => answers[q.id] !== survey.answers[q.id]);

  async function save() {
    setSaving(true);
    setSaveError(null);
    setSaved(null);
    try {
      const payload = Object.entries(answers).map(([question_id, choice]) => ({
        question_id,
        choice,
      }));
      const r = await postSurvey(payload);
      if (!r.ok) {
        setSaveError(`POST /profile/survey: ${r.error}`);
        return;
      }
      setSaved(
        `${r.data.answered} of ${r.data.total} answered. ` +
          (r.data.answered < r.data.total
            ? `The ${r.data.total - r.data.answered} unanswered contribute nothing and lower the profile confidence — they are not imputed.`
            : "Every trade-off is answered."),
      );
      await load();
      onSaved?.();
    } finally {
      setSaving(false);
    }
  }

  return (
    <Panel
      title="the twelve trade-offs"
      subtitle="Forced choices, not agreement scales. Each option carries the signals it implies; picking neither is a legitimate answer and is recorded as absence."
      right={
        <div className="min-w-[190px]">
          <ProgressBar
            value={answeredCount / Math.max(1, survey.total)}
            label={`${answeredCount} OF ${survey.total} ANSWERED`}
          />
        </div>
      }
    >
      <ol className="grid gap-5">
        {survey.questions.map((q, i) => {
          const chosen = answers[q.id];
          return (
            <li key={q.id} className="border-t border-[color:var(--rule)] pt-4 first:border-t-0 first:pt-0">
              <div className="meta text-[color:var(--muted)]">
                {String(i + 1).padStart(2, "0")} / {survey.total}
                {!chosen && " · UNANSWERED"}
              </div>
              <p className="body-t mt-1.5 max-w-[68ch]">{q.prompt}</p>
              <div className="mt-3 grid gap-3 md:grid-cols-2">
                {(["a", "b"] as const).map((side) => {
                  const opt = side === "a" ? q.option_a : q.option_b;
                  const isChosen = chosen === side;
                  return (
                    <button
                      key={side}
                      type="button"
                      aria-pressed={isChosen}
                      onClick={() =>
                        setAnswers((prev) => ({ ...prev, [q.id]: side }))
                      }
                      className="border px-4 py-3 text-left"
                      style={{
                        // The chosen side is marked by colour AND by a filled leading
                        // rule, so the state survives being read without colour.
                        borderColor: isChosen ? "var(--accent)" : "var(--rule)",
                        color: isChosen ? "var(--accent)" : "var(--figure)",
                        borderLeftWidth: isChosen ? 3 : 1,
                        background: isChosen ? "var(--ink-09)" : "transparent",
                      }}
                    >
                      <span className="meta text-[color:var(--muted)]">
                        OPTION {side.toUpperCase()}
                      </span>
                      <span className="mt-1 block text-[15px] leading-snug">
                        {opt.text}
                      </span>
                      <span className="meta mt-2 block text-[color:var(--muted)]">
                        {Object.entries(opt.signals)
                          .map(([k, v]) => `${k} ${v > 0 ? "+" : ""}${v}`)
                          .join(" · ") || "NO SIGNALS"}
                      </span>
                    </button>
                  );
                })}
              </div>
            </li>
          );
        })}
      </ol>

      <div className="mt-6 border-t border-[color:var(--rule)] pt-4">
        {saveError && <ErrorNote message={saveError} onRetry={() => void save()} />}
        {saved && (
          <p className="caption mb-3 max-w-none text-[color:var(--muted)]">{saved}</p>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={() => void save()}
            disabled={saving || answeredCount === 0 || !dirty}
            className="meta border px-4 py-2 disabled:opacity-50"
            style={{ borderColor: "var(--accent)", color: "var(--accent)" }}
          >
            SAVE {answeredCount} ANSWER{answeredCount === 1 ? "" : "S"}
          </button>
          {saving && (
            <Busy className="min-w-[200px]" label="SAVING…" budgetMs={TIMEOUT.read} />
          )}
          {!saving && !dirty && answeredCount > 0 && (
            <span className="caption max-w-none text-[color:var(--muted)]">
              Saved. {unanswered.length} unanswered.
            </span>
          )}
          {!saving && answeredCount === 0 && (
            <span className="caption max-w-none text-[color:var(--muted)]">
              Nothing answered yet. Partial submissions are legal — answer what you have
              a real opinion about.
            </span>
          )}
        </div>
      </div>
    </Panel>
  );
}
