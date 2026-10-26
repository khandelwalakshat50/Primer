import { useEffect, useMemo } from "react";
import { Button } from "../ui";
import { courseIsBuilding } from "../lessonCache";
import type { CachedLesson, ReelMetrics, StepMetric } from "../types";

type Props = {
  lesson: CachedLesson | null;
  onClose: () => void;
  onStop?: () => void;
};

const EMPTY: ReelMetrics = { steps: [] };

function secs(n: number | undefined): string {
  if (n === undefined || n === null) return "—";
  return n >= 100 ? `${Math.round(n)}s` : `${n.toFixed(1)}s`;
}

/** Fixed conversion for display. Not a live quote — if the real rate moves,
 *  change it here and every figure in this panel follows. */
const USD_TO_INR = 95;

function money(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  if (n === 0) return "$0";
  return n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(3)}`;
}

function rupees(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  const v = n * USD_TO_INR;
  if (v === 0) return "₹0";
  if (v >= 1000) return `₹${Math.round(v).toLocaleString("en-IN")}`;
  if (v < 0.1) return `₹${v.toFixed(3)}`;
  return `₹${v.toFixed(2)}`;
}

/** Cost in both currencies, stacked so the narrow table column still fits. */
function Cost({ usd }: { usd: number | null | undefined }) {
  if (usd === null || usd === undefined) return <>—</>;
  return (
    <>
      <span className="prod-usd">{money(usd)}</span>
      <span className="prod-inr">{rupees(usd)}</span>
    </>
  );
}

function tokens(n: number | undefined): string {
  if (!n) return "0";
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

/** Roll several lessons' metrics into one record for a course total. */
function combine(all: ReelMetrics[]): ReelMetrics {
  const byStep = new Map<string, StepMetric>();
  const order: string[] = [];
  for (const m of all) {
    for (const s of m.steps || []) {
      const prev = byStep.get(s.step);
      if (!prev) {
        byStep.set(s.step, { ...s });
        order.push(s.step);
      } else {
        const costs = [prev.cost_usd, s.cost_usd].filter((c): c is number => c !== null);
        byStep.set(s.step, {
          ...prev,
          seconds: Number((prev.seconds + s.seconds).toFixed(3)),
          input_tokens: prev.input_tokens + s.input_tokens,
          output_tokens: prev.output_tokens + s.output_tokens,
          calls: prev.calls + s.calls,
          cost_usd: costs.length ? Number(costs.reduce((a, b) => a + b, 0).toFixed(6)) : null,
        });
      }
    }
  }
  const steps = order.map((k) => byStep.get(k)!);
  const priced = steps.map((s) => s.cost_usd).filter((c): c is number => c !== null);
  return {
    steps,
    llm_seconds: Number(steps.reduce((n, s) => n + s.seconds, 0).toFixed(1)),
    input_tokens: steps.reduce((n, s) => n + s.input_tokens, 0),
    output_tokens: steps.reduce((n, s) => n + s.output_tokens, 0),
    calls: steps.reduce((n, s) => n + s.calls, 0),
    cost_usd: priced.length ? Number(priced.reduce((a, b) => a + b, 0).toFixed(6)) : null,
    scripted_seconds: all.reduce((n, m) => n + (m.scripted_seconds || 0), 0) || undefined,
    directed_seconds: all.reduce((n, m) => n + (m.directed_seconds || 0), 0) || undefined,
    unpriced_models: Array.from(new Set(all.flatMap((m) => m.unpriced_models || []))),
  };
}

function StepTable({ metrics, emptyNote }: { metrics: ReelMetrics; emptyNote?: string }) {
  const steps = metrics.steps || [];
  const slowest = Math.max(1, ...steps.map((s) => s.seconds));
  if (!steps.length) {
    return (
      <p className="prod-empty">
        {emptyNote || "Generated, but no timing was recorded for this lesson."}
      </p>
    );
  }
  return (
    <table className="prod-table">
      <thead>
        <tr>
          <th>Step</th>
          <th className="num">Time</th>
          <th className="num">Calls</th>
          <th className="num">In</th>
          <th className="num">Out</th>
          <th className="num">Cost</th>
        </tr>
      </thead>
      <tbody>
        {steps.map((s) => (
          <tr key={s.step} className={s.ok ? "" : "is-failed"}>
            <td>
              <span className="prod-step">{s.label || s.step}</span>
              <span className="prod-model">
                {s.provider} {s.model}
              </span>
              <span className="prod-bar" style={{ width: `${(s.seconds / slowest) * 100}%` }} />
            </td>
            <td className="num">{secs(s.seconds)}</td>
            <td className="num">{s.calls}</td>
            <td className="num">{tokens(s.input_tokens)}</td>
            <td className="num">{tokens(s.output_tokens)}</td>
            <td className="num">
              <Cost usd={s.cost_usd} />
            </td>
          </tr>
        ))}
      </tbody>
      <tfoot>
        <tr>
          <td>Total LLM work</td>
          <td className="num">{secs(metrics.llm_seconds)}</td>
          <td className="num">{metrics.calls ?? 0}</td>
          <td className="num">{tokens(metrics.input_tokens)}</td>
          <td className="num">{tokens(metrics.output_tokens)}</td>
          <td className="num">
            <Cost usd={metrics.cost_usd} />
          </td>
        </tr>
      </tfoot>
    </table>
  );
}

export function ProductionPanel({ lesson, onClose, onStop }: Props) {
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const isCourse = lesson?.kind === "course" && Boolean(lesson.curriculum);

  const perLesson = useMemo(() => {
    if (!lesson) return [];
    if (isCourse) {
      return (lesson.curriculum?.reels || []).map((spec, i) => ({
        key: spec.reel_id,
        name: `${i + 1}. ${spec.title}`,
        status: spec.status,
        metrics: spec.metrics || spec.reel?.metrics || EMPTY,
      }));
    }
    return [
      {
        key: "reel",
        name: lesson.title || "Reel",
        status: "ready",
        metrics: lesson.reel?.metrics || EMPTY,
      },
    ];
  }, [lesson, isCourse]);

  const total = useMemo(() => combine(perLesson.map((l) => l.metrics)), [perLesson]);
  const measured = perLesson.filter((l) => (l.metrics.steps || []).length > 0).length;
  const unpriced = total.unpriced_models || [];

  const generating =
    Boolean(
      lesson?.kind === "course" && lesson.curriculum && courseIsBuilding(lesson.curriculum),
    ) || Boolean(lesson?.kind === "reel" && !lesson.reel && onStop);

  // Wall time is less than summed LLM time because SVG renders run in parallel.
  const wall = (total.scripted_seconds || 0) + (total.directed_seconds || 0);

  return (
    <>
      <div className="script-backdrop" onClick={onClose} />
      <aside className="prod-panel" role="dialog" aria-modal="true" aria-label="Production metrics">
        <header className="script-head">
          <div>
            <p className="script-title">Production · {lesson?.question || "—"}</p>
            <p className="script-meta">
              {isCourse ? `${perLesson.length} lessons` : "single reel"} · {total.calls ?? 0} model calls
              {wall ? ` · ${secs(Number(wall.toFixed(1)))} wall` : ""}
            </p>
          </div>
          <div className="script-head-actions">
          {generating && onStop ? (
            <Button
              variant="ghost"
              onClick={() => {
                onStop();
                onClose();
              }}
            >
              Stop generation
            </Button>
          ) : null}
          <Button variant="ghost" onClick={onClose} aria-label="Close">
            Close
          </Button>
          </div>
        </header>

        <div className="script-body">
          <div className="prod-cards">
            <div className="prod-card">
              <span className="prod-card-k">LLM time</span>
              <span className="prod-card-v">{secs(total.llm_seconds)}</span>
              <span className="prod-card-n">summed across calls</span>
            </div>
            <div className="prod-card">
              <span className="prod-card-k">Wall time</span>
              <span className="prod-card-v">{wall ? secs(Number(wall.toFixed(1))) : "—"}</span>
              <span className="prod-card-n">renders run in parallel</span>
            </div>
            <div className="prod-card">
              <span className="prod-card-k">Tokens</span>
              <span className="prod-card-v">
                {tokens(total.input_tokens)}
                <span className="prod-card-split"> / </span>
                {tokens(total.output_tokens)}
              </span>
              <span className="prod-card-n">in / out</span>
            </div>
            <div className="prod-card">
              <span className="prod-card-k">Cost</span>
              <span className="prod-card-v">{money(total.cost_usd)}</span>
              <span className="prod-card-inr">{rupees(total.cost_usd)}</span>
              <span className="prod-card-n">
                {unpriced.length ? "partly unpriced" : `at \u20b9${USD_TO_INR}/$`}
              </span>
            </div>
          </div>

          {unpriced.length ? (
            <p className="prod-warn">
              No price configured for <strong>{unpriced.join(", ")}</strong>, so cost is not counted for
              those calls. Set real rates with the <code>MODEL_PRICES</code> env var, e.g.{" "}
              <code>{`{"${unpriced[0]}":{"in":1.25,"out":10}}`}</code> (USD per 1M tokens).
            </p>
          ) : null}

          <h3 className="prod-h">
            {isCourse ? `Total, by step — ${measured} of ${perLesson.length} lessons` : "By step"}
          </h3>
          {(total.steps || []).some((s) => s.step === "sora_render" || s.step === "image_render") ? (
            <p className="prod-note">
              Mixed-media reels bill Sora per second of generated video (sora-2 at 720p is $0.10/sec)
              and OpenAI stills per image. Those lines are included in the total cost above.
            </p>
          ) : null}
          {isCourse && measured < perLesson.length ? (
            <p className="prod-note">
              {perLesson.length - measured} lesson
              {perLesson.length - measured === 1 ? "" : "s"} produced no metrics, so the totals below
              cover only the {measured} measured lesson{measured === 1 ? "" : "s"} — not the whole course.
            </p>
          ) : null}
          <StepTable metrics={total} />

          {isCourse ? (
            <>
              <h3 className="prod-h">Per lesson</h3>
              {perLesson.map((l) => (
                <section key={l.key} className="prod-lesson">
                  <div className="prod-lesson-head">
                    <span className="prod-lesson-name">{l.name}</span>
                    <span className="prod-lesson-sum">
                      {secs(l.metrics.llm_seconds)} · {tokens(l.metrics.input_tokens)}/
                      {tokens(l.metrics.output_tokens)} tok · {money(l.metrics.cost_usd)}
                      {l.metrics.cost_usd !== null && l.metrics.cost_usd !== undefined
                        ? ` / ${rupees(l.metrics.cost_usd)}`
                        : ""}
                      {l.status !== "ready" ? ` · ${l.status}` : ""}
                    </span>
                  </div>
                  <StepTable
                    metrics={l.metrics}
                    emptyNote={
                      l.status === "ready"
                        ? undefined
                        : l.status === "failed"
                          ? "This lesson failed, so no production data was recorded."
                          : "Not generated yet — numbers appear once this lesson is built."
                    }
                  />
                </section>
              ))}
            </>
          ) : null}
        </div>
      </aside>
    </>
  );
}
