"use client";

/**
 * Decision-history upload — the REVEALED half of the profile (§2.1).
 *
 * The rule this component exists to enforce: an upload that partly failed must READ as
 * partly failed. The API reports every row it could not parse, and the honest result is
 * "13 of 16 rows accepted, 3 rejected" with the reason and the raw text for each — not a
 * green tick over a file that lost a fifth of its rows on the way in. A parser that
 * quietly discards rows produces a confident profile of nothing.
 *
 * Warnings are shown SEPARATELY from rejections, because they are a different fact: the
 * row was accepted and one optional field (usually a date) could not be read. Folding
 * them into the rejection count would overstate the damage; hiding them would understate
 * it.
 *
 * A re-upload REPLACES the history rather than appending — the API's default, and the
 * right one: a second upload of a corrected file is a correction, and appending would
 * double every row. That is stated on screen, because it is surprising if unsaid.
 */

import { useRef, useState } from "react";
import { postDecisions, type UploadResult } from "@/lib/vc";
import { Busy, ErrorNote, Panel, Stat } from "@/components/ui";
import { TIMEOUT } from "@/lib/api";

const MAX_BYTES = 2 * 1024 * 1024;

export default function DecisionUpload({ onUploaded }: { onUploaded?: () => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<UploadResult | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  async function send(file: File) {
    setError(null);
    setResult(null);
    setFileName(file.name);

    if (file.size > MAX_BYTES) {
      setError(
        `${file.name} is ${(file.size / 1024 / 1024).toFixed(1)}MB; the upload limit is 2MB.`,
      );
      return;
    }

    setBusy(true);
    try {
      // Read as text and post the body. The API detects CSV vs JSON from the CONTENT,
      // so a file named .csv that holds JSON still parses — an extension is a claim,
      // not a fact.
      const text = await file.text();
      const r = await postDecisions(text);
      if (!r.ok) {
        setError(
          r.status === 401
            ? "a decision history belongs to an account — sign in to upload one"
            : `POST /profile/decisions: ${r.error}`,
        );
        return;
      }
      setResult(r.data.upload);
      onUploaded?.();
    } catch (e) {
      setError(`could not read ${file.name}: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  const rejected = result?.rejected ?? [];
  const warnings = result?.warnings ?? [];

  return (
    <Panel
      title="past decisions"
      subtitle="CSV or JSON. Columns: company, sector, stage, decision (invested | passed | watched), date, rationale, outcome. A re-upload REPLACES the history — it does not append."
    >
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          const file = e.dataTransfer.files?.[0];
          if (file) void send(file);
        }}
        className="border border-dashed px-5 py-8 text-center"
        style={{ borderColor: dragging ? "var(--accent)" : "var(--rule)" }}
      >
        <p className="mono text-[15px]">Drop a CSV or JSON file here</p>
        <p className="caption mx-auto mt-1.5 text-[color:var(--muted)]">
          Or choose one. Nothing is derived from a file that has not been uploaded — there
          is no sample history behind this control.
        </p>
        <input
          ref={inputRef}
          type="file"
          accept=".csv,.json,text/csv,application/json,text/plain"
          className="sr-only"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void send(file);
            // Cleared so re-selecting the same file after a fix still fires onChange.
            e.target.value = "";
          }}
        />
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={busy}
          className="meta mt-4 border px-4 py-2 disabled:opacity-50"
          style={{ borderColor: "var(--accent)", color: "var(--accent)" }}
        >
          CHOOSE A FILE
        </button>
      </div>

      {busy && (
        <Busy
          className="mt-4"
          label={`PARSING ${fileName ?? "UPLOAD"}…`}
          budgetMs={TIMEOUT.query}
          stages={["reading the file", "parsing rows", "recomputing the derived profile"]}
        />
      )}

      {error && (
        <div className="mt-4">
          <ErrorNote message={error} />
        </div>
      )}

      {result && (
        <div className="mt-5 border-t border-[color:var(--rule)] pt-5">
          <p className="body-t">
            <span className="mono">
              {result.accepted} of {result.total_rows}
            </span>{" "}
            rows accepted
            {rejected.length > 0 && (
              <>
                , <span className="mono">{rejected.length}</span> rejected
              </>
            )}
            {warnings.length > 0 && (
              <>
                , <span className="mono">{warnings.length}</span> accepted with a lost
                field
              </>
            )}
            .
          </p>

          <div className="mt-3 grid gap-3 sm:grid-cols-3">
            <Stat label="ACCEPTED" value={result.accepted} sub={fileName ?? undefined} />
            <Stat
              label="REJECTED"
              value={rejected.length}
              sub={rejected.length ? "not counted in any derivation" : "every row parsed"}
              // The rationed colour is not spent here: a rejected row is a row we could
              // not read, not an assertion contradicted by evidence.
            />
            <Stat
              label="WARNINGS"
              value={warnings.length}
              sub={warnings.length ? "accepted, one optional field dropped" : "none"}
            />
          </div>

          {rejected.length > 0 && (
            <div className="mt-5">
              <h3 className="meta text-[color:var(--figure)]">REJECTED ROWS</h3>
              <ul className="mt-2 grid gap-2">
                {rejected.map((row) => (
                  <li
                    key={`r-${row.row_number}`}
                    className="border border-dashed border-[color:var(--figure)] px-3 py-2"
                  >
                    <div className="meta text-[color:var(--figure)]">
                      ROW {row.row_number} · {row.reason}
                    </div>
                    <pre className="mono mt-1.5 overflow-x-auto text-[12px] text-[color:var(--muted)]">
                      {row.raw}
                    </pre>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {warnings.length > 0 && (
            <div className="mt-5">
              <h3 className="meta text-[color:var(--figure)]">
                ACCEPTED, WITH A FIELD DROPPED
              </h3>
              <ul className="mt-2 grid gap-2">
                {warnings.map((row) => (
                  <li
                    key={`w-${row.row_number}`}
                    className="border border-[color:var(--rule)] px-3 py-2"
                  >
                    <div className="meta text-[color:var(--muted)]">
                      ROW {row.row_number} · {row.reason}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}
