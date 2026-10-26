import { useEffect, useState } from "react";
import { ReelPlayer } from "./ReelPlayer";
import {
  fetchLabDiff,
  fetchLabRun,
  pauseLabRun,
  promoteFromRun,
  resumeLabRun,
  retryLabRun,
  stopLabRun,
} from "../labApi";
import type { DiffLine, EvolutionIteration, EvolutionRun, PromptDiff } from "../labTypes";
import type { ReelPlan } from "../types";

const DIMS = [
  ["teaching_quality", "Teaching"],
  ["coherence", "Coherence"],
  ["visual_continuity", "Continuity"],
  ["entertainment", "Entertainment"],
  ["pacing", "Pacing"],
  ["originality", "Originality"],
  ["visual_clarity", "Clarity"],
  ["narration_quality", "Narration"],
  ["phone_readability", "Phone"],
] as const;

type Panel = "play" | "prompt" | "critique" | "changes";

function asReel(raw: unknown): ReelPlan | null {
  if (!raw || typeof raw !== "object") return null;
  const reel = raw as ReelPlan;
  if (!reel.hook || !reel.payoff) return null;
  return reel;
}

function ScoreChart({ run }: { run: EvolutionRun }) {
  const points = run.iterations.filter((it) => it.status === "complete");
  if (!points.length) return <p className="lab-muted">Scores appear as iterations finish.</p>;
  const max = 10;
  const w = 320;
  const h = 120;
  const step = points.length === 1 ? w / 2 : w / (points.length - 1);
  const ys = points.map((p) => h - (p.score / max) * (h - 16) - 8);
  const d = points
    .map((p, i) => `${i === 0 ? "M" : "L"} ${i * step} ${ys[i]}`)
    .join(" ");
  const best = points.find((p) => p.is_best) || points.reduce((a, b) => (a.score >= b.score ? a : b));
  return (
    <svg className="lab-chart" viewBox={`0 0 ${w} ${h}`} role="img" aria-label="Score by iteration">
      <line x1="0" y1={h - 8} x2={w} y2={h - 8} stroke="#E5E5E5" />
      <path d={d} fill="none" stroke="#171717" strokeWidth="2.5" />
      {points.map((p, i) => (
        <g key={p.iteration}>
          <circle
            cx={i * step}
            cy={ys[i]}
            r={p.is_best ? 6 : 4}
            fill={p.is_best ? "#171717" : "#A3A3A3"}
          />
          <text x={i * step} y={h - 8 + 14} textAnchor="middle" fontSize="10" fill="#737373">
            {p.iteration}
          </text>
        </g>
      ))}
      <text x={w} y="12" textAnchor="end" fontSize="11" fill="#171717">
        best {best.score.toFixed(1)}
      </text>
    </svg>
  );
}

function DiffView({ lines }: { lines: DiffLine[] }) {
  const interesting = lines.filter((row) => row.op !== "same");
  const shown = interesting.length ? interesting : lines.slice(0, 12);
  return (
    <pre className="lab-diff">
      {shown.map((row, i) => (
        <span key={i} className={`lab-diff-${row.op}`}>
          {row.op === "added" ? "+" : row.op === "removed" ? "−" : " "} {row.text}
          {"\n"}
        </span>
      ))}
    </pre>
  );
}

function IterationCard({
  run,
  item,
  promptText,
  onPlay,
  onCompare,
}: {
  run: EvolutionRun;
  item: EvolutionIteration;
  promptText: string;
  onPlay: (reel: ReelPlan, playId: string) => void;
  onCompare: (n: number) => void;
}) {
  const [panel, setPanel] = useState<Panel>("critique");
  const optimizer = item.optimizer || {};
  const firstReel = asReel(item.questions[0]?.reel);
  return (
    <article className={`lab-iter ${item.is_best ? "is-best" : ""} ${item.status}`}>
      <header className="lab-iter-head">
        <p className="lab-kicker">Iteration {item.iteration}</p>
        <h3>
          Prompt v{item.prompt_version}
          {item.is_best ? <span className="lab-badge">Best version</span> : null}
          {item.accepted ? null : item.status === "complete" ? (
            <span className="lab-badge is-reject">Rejected as baseline</span>
          ) : null}
        </h3>
        <p className="lab-score-lg">
          {item.status === "complete" ? item.score.toFixed(1) : item.status}
          {item.status === "complete" && item.delta ? (
            <span className={item.delta >= 0 ? "is-up" : "is-down"}>
              {item.delta >= 0 ? " ↑" : " ↓"} {Math.abs(item.delta).toFixed(1)}
            </span>
          ) : null}
        </p>
      </header>
      {item.error ? <p className="desk-error">{item.error}</p> : null}
      <div className="lab-dims">
        {DIMS.map(([key, label]) => (
          <span key={key}>
            {label} {(item.scores[key] ?? 0).toFixed(1)}
          </span>
        ))}
      </div>
      {item.questions.length > 1 ? (
        <ul className="lab-q-scores">
          {item.questions.map((q) => (
            <li key={q.question}>
              <strong>{(q.scores.overall_score ?? 0).toFixed(1)}</strong> {q.question}
            </li>
          ))}
        </ul>
      ) : null}
      <div className="lab-iter-actions">
        <button type="button" className={panel === "play" ? "is-on" : ""} onClick={() => setPanel("play")}>
          Play
        </button>
        <button type="button" className={panel === "prompt" ? "is-on" : ""} onClick={() => setPanel("prompt")}>
          View prompt
        </button>
        <button type="button" className={panel === "critique" ? "is-on" : ""} onClick={() => setPanel("critique")}>
          Critique
        </button>
        <button type="button" className={panel === "changes" ? "is-on" : ""} onClick={() => setPanel("changes")}>
          Changes
        </button>
        <button type="button" onClick={() => onCompare(item.iteration)}>
          Compare
        </button>
      </div>
      {panel === "play" ? (
        <div className="lab-play-row">
          {item.questions.map((q) => {
            const reel = asReel(q.reel);
            return reel ? (
              <div key={q.question} className="lab-play-card">
                <p className="lab-muted">{q.question}</p>
                <button type="button" onClick={() => onPlay(reel, `${item.iteration}:${q.question}`)}>
                  Open in phone
                </button>
              </div>
            ) : (
              <p key={q.question} className="lab-muted">
                No reel yet.
              </p>
            );
          })}
          {firstReel ? (
            <div className="lab-phone-wrap">
              <ReelPlayer plan={firstReel} voice={false} playId={item.iteration} />
            </div>
          ) : null}
        </div>
      ) : null}
      {panel === "prompt" ? <pre className="lab-prompt">{promptText}</pre> : null}
      {panel === "critique" ? (
        <div className="lab-critique">
          <p className="lab-kicker">Failure modes</p>
          <ul>
            {(item.failure_modes.length ? item.failure_modes : ["None recorded"]).map((row) => (
              <li key={row}>{row}</li>
            ))}
          </ul>
          <p className="lab-kicker">Strengths</p>
          <ul>
            {(item.strengths.length ? item.strengths : ["—"]).map((row) => (
              <li key={row}>{row}</li>
            ))}
          </ul>
          <p className="lab-kicker">Recommendations</p>
          <ul>
            {(item.recommendations.length ? item.recommendations : ["—"]).map((row) => (
              <li key={row}>{row}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {panel === "changes" ? (
        <div className="lab-critique">
          <p className="lab-kicker">Why this changed</p>
          <p>{String(optimizer.diagnosis || optimizer.reasoning || item.recommendations[0] || "Waiting on optimizer.")}</p>
          <p className="lab-kicker">Expected impact</p>
          <p>{String(optimizer.expected_impact || "—")}</p>
          <ul>
            {((optimizer.proposed_changes as string[]) || []).map((row) => (
              <li key={row}>{row}</li>
            ))}
          </ul>
          {optimizer.error ? <p className="desk-error">{String(optimizer.error)}</p> : null}
        </div>
      ) : null}
    </article>
  );
}

export function LabRunDetail({
  runId,
  onBack,
}: {
  runId: string;
  onBack: () => void;
}) {
  const [run, setRun] = useState<EvolutionRun | null>(null);
  const [error, setError] = useState("");
  const [diff, setDiff] = useState<PromptDiff | null>(null);
  const [compare, setCompare] = useState<[number, number] | null>(null);
  const [busy, setBusy] = useState("");

  useEffect(() => {
    let live = true;
    async function load() {
      try {
        const next = await fetchLabRun(runId);
        if (live) setRun(next);
      } catch (err) {
        if (live) setError(err instanceof Error ? err.message : "Could not load run.");
      }
    }
    void load();
    const id = window.setInterval(load, 2000);
    return () => {
      live = false;
      window.clearInterval(id);
    };
  }, [runId]);

  useEffect(() => {
    if (!run) return;
    const from = run.selected_version;
    const to = run.best_version;
    if (!to || from === to) return;
    void fetchLabDiff(run.run_id, from, to)
      .then(setDiff)
      .catch(() => undefined);
  }, [run]);

  if (!run) {
    return error ? (
      <div className="lab-detail">
        <button type="button" className="lab-text-btn" onClick={onBack}>
          ← Runs
        </button>
        <p className="desk-error">{error}</p>
      </div>
    ) : (
      <p className="lab-muted">Opening the experiment…</p>
    );
  }

  const live = ["queued", "running", "paused"].includes(run.status);
  const usage = run.usage || {};

  async function act(name: string, fn: () => Promise<unknown>) {
    setBusy(name);
    setError("");
    try {
      await fn();
      setRun(await fetchLabRun(runId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Action failed.");
    } finally {
      setBusy("");
    }
  }

  const compareIters = compare
    ? run.iterations.filter((it) => compare.includes(it.iteration) && it.status === "complete")
    : [];

  return (
    <div className="lab-detail">
      <div className="lab-detail-bar">
        <button type="button" className="lab-text-btn" onClick={onBack}>
          ← Runs
        </button>
        <div className="lab-controls">
          {live && run.status !== "paused" ? (
            <button type="button" disabled={Boolean(busy)} onClick={() => act("pause", () => pauseLabRun(runId))}>
              Pause
            </button>
          ) : null}
          {run.status === "paused" ? (
            <button type="button" disabled={Boolean(busy)} onClick={() => act("resume", () => resumeLabRun(runId))}>
              Resume
            </button>
          ) : null}
          {live ? (
            <button type="button" disabled={Boolean(busy)} onClick={() => act("stop", () => stopLabRun(runId))}>
              Stop
            </button>
          ) : null}
          {run.status === "failed" ? (
            <button type="button" disabled={Boolean(busy)} onClick={() => act("retry", () => retryLabRun(runId))}>
              Retry failed
            </button>
          ) : null}
          {run.best_version ? (
            <button
              type="button"
              disabled={Boolean(busy)}
              onClick={() => act("promote", () => promoteFromRun(run.prompt_set_id, run.best_version))}
            >
              Use as active prompt
            </button>
          ) : null}
        </div>
      </div>

      <header className="lab-hero">
        <p className="lab-kicker">Test {run.test_set_name || "question"}</p>
        <h2>{run.questions[0]}</h2>
        {run.questions.length > 1 ? (
          <p className="lab-muted">{run.questions.length} questions in this set — scores are averages.</p>
        ) : null}
        <div className="lab-select-card">
          <p className="lab-kicker">Prompt selected</p>
          <h3>
            {run.prompt_topic} Director v{run.selected_version}
          </h3>
          <p>{run.selection_reason}</p>
          <p className="lab-muted">Fallback: {run.fallback ? "Yes" : "No"}</p>
        </div>
      </header>

      <section className="lab-progress-block">
        <p>
          Iteration {run.iteration_count} / {run.max_iterations} · {run.status.replaceAll("_", " ")}
          {run.stop_reason ? ` · ${run.stop_reason.replaceAll("_", " ")}` : ""}
        </p>
        <div className="lab-bar">
          <span style={{ width: `${Math.min(100, (run.iteration_count / run.max_iterations) * 100)}%` }} />
        </div>
        <p className="lab-muted">
          Generations {usage.generations ?? 0} · Critiques {usage.critiques ?? 0} · Optimizations{" "}
          {usage.optimizations ?? 0} · Estimated model calls {usage.estimated_model_calls ?? 0}
        </p>
        {run.error ? <p className="desk-error">{run.error}</p> : null}
        <ScoreChart run={run} />
      </section>

      <ol className="lab-timeline">
        <li>
          <p className="lab-kicker">Question</p>
          <p>{run.questions.join(" · ")}</p>
        </li>
        <li>
          <p className="lab-kicker">Prompt selected</p>
          <p>
            {run.prompt_topic} v{run.selected_version} — {run.selection_reason}
          </p>
        </li>
        {run.iterations.map((item) => (
          <li key={item.iteration}>
            <IterationCard
              run={run}
              item={item}
              promptText={item.prompt_text || `Version ${item.prompt_version}`}
              onPlay={() => undefined}
              onCompare={(n) => setCompare([run.best_iteration || 1, n])}
            />
          </li>
        ))}
      </ol>

      {diff ? (
        <section className="lab-card">
          <p className="lab-kicker">Prompt changes v{diff.from_version} → v{diff.to_version}</p>
          <p className="lab-why">{diff.diagnosis || diff.reason}</p>
          <DiffView lines={diff.lines} />
        </section>
      ) : null}

      {compareIters.length === 2 ? (
        <section className="lab-compare">
          <div className="lab-compare-head">
            <p className="lab-kicker">Compare</p>
            <button type="button" className="lab-text-btn" onClick={() => setCompare(null)}>
              Close
            </button>
          </div>
          {compareIters.map((item) => {
            const reel = asReel(item.questions[0]?.reel);
            return (
              <div key={item.iteration} className="lab-compare-col">
                <h3>
                  Iteration {item.iteration} · {item.score.toFixed(1)}
                </h3>
                {reel ? (
                  <div className="lab-phone-wrap">
                    <ReelPlayer plan={reel} voice={false} playId={item.iteration + 100} />
                  </div>
                ) : null}
              </div>
            );
          })}
        </section>
      ) : null}
    </div>
  );
}
