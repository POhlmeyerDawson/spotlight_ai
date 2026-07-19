"use client";

/**
 * Route `/thesis` — where the fund's STANDING policy is edited.
 *
 * This used to sit on top of `/pipeline`, stacked above the natural-language query box.
 * Two controls, side by side, doing categorically different things: the query dims rows,
 * the thesis deletes them from the pipeline. Collapsing the pipeline to one input meant
 * choosing which semantic the single box carries — it carries FILTER — and giving the
 * other one a home of its own rather than deleting a fund's real, persistent state.
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import { getThesis, type Result } from "@/lib/api";
import type { Thesis } from "@/lib/types";
import Shell from "@/components/Shell";
import ThesisPanel from "@/components/ThesisPanel";
import { Loading, SourceChip } from "@/components/ui";

export default function ThesisPage() {
  const [thesis, setThesis] = useState<Result<Thesis> | null>(null);

  useEffect(() => {
    let live = true;
    void getThesis().then((t) => {
      if (live) setThesis(t);
    });
    return () => {
      live = false;
    };
  }, []);

  if (!thesis) {
    return (
      <Shell title="thesis">
        <Loading label="thesis" stages={["reading the standing thesis…"]} />
      </Shell>
    );
  }

  return (
    <Shell
      title="thesis"
      lede={
        <>
          What this fund looks at, at all. Editing it changes which companies reach the
          pipeline — it never changes what is true about a founder.
        </>
      }
      right={<SourceChip source={thesis.source} note={thesis.note} />}
      meta={
        <>
          S0
          <br />
          CONFIG
        </>
      }
    >
      <div className="space-y-5">
        <div className="border border-[color:var(--rule)] px-5 py-4">
          <h2 className="meta text-[color:var(--figure)]">
            This is not the same thing as a query
          </h2>
          <p className="caption mt-1.5 max-w-none text-[color:var(--muted)]">
            The compound query on{" "}
            <Link href="/pipeline" className="text-[color:var(--accent)] underline">
              /pipeline
            </Link>{" "}
            narrows what you are <em>looking at</em>: non-matching rows are dimmed, and
            clearing the query brings them straight back. This page sets what the fund
            looks at <em>at all</em>. A sector removed here stops being sourced, scored
            and shown, and no query can bring it back. That is why the two are separate
            pages and why promoting a query to the thesis is an explicit action rather
            than a side effect of typing.
          </p>
        </div>

        <ThesisPanel initial={thesis.data} />
      </div>
    </Shell>
  );
}
