import { useEffect, useState } from "react";
import { fetchLabOverview, startLabRun } from "../labApi";
import type { LabOverview, RunSummary } from "../labTypes";
import { Button, Field, Segmented } from "../ui";
import { ApiRoster } from "./ApiRoster";
import { LabLibrary } from "./LabLibrary";
import { LabRunDetail } from "./LabRunDetail";

type Tab = "lab" | "library";

function statusTone(status: string) {
  if (status === "running" || status === "queued") return "is-live";
  if (status === "paused") return "is-pause";
  if (status === "failed") return "is-fail";
  return "is-done";
}

function RunCard({ run, onOpen }: { run: RunSummary; onOpen: () => void }) {
  const pct = Math.min(100, (run.iteration_count / Math.max(1, run.max_iterations)) * 100);
  return (
    <button type="button" className={`lab-card lab-run-card ${statusTone(run.status)}`} onClick={onOpen}>
      <p className="lab-kicker">
        {run.prompt_topic} · v{run.selected_version}
        {run.best_version !== run.selected_version ? ` → v${run.best_version}` : ""}
      </p>
      <h3>{run.questions[0] || "Untitled experiment"}</h3>
      <p className="lab-muted">
        {run.status.replace(/_/g, " ")} · iteration {run.iteration_count}/{run.max_iterations}
        {run.best_score >= 0 && run.iteration_count ? ` · ${run.best_score.toFixed(1)}` : ""}
      </p>
      <div className="lab-bar">
        <span style={{ width: `${pct}%` }} />
      </div>
    </button>
  );
}

export function PromptLab() {
  const [tab, setTab] = useState<Tab>("lab");
  const [overview, setOverview] = useState<LabOverview | null>(null);
  const [openRun, setOpenRun] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [question, setQuestion] = useState("");
  const [moreQuestions, setMoreQuestions] = useState("");
  const [maxIter, setMaxIter] = useState(5);
  const [testSetId, setTestSetId] = useState("");
  const [promptSetId, setPromptSetId] = useState("");
  const [starting, setStarting] = useState(false);

  async function refresh() {
    const data = await fetchLabOverview();
    setOverview(data);
  }

  useEffect(() => {
    void refresh().catch((err) =>
      setError(err instanceof Error ? err.message : "Could not load Prompt Lab. Is the backend running on 8005?"),
    );
    const id = window.setInterval(() => {
      void refresh().catch(() => undefined);
    }, 2500);
    return () => window.clearInterval(id);
  }, []);

  if (openRun) {
    return <LabRunDetail runId={openRun} onBack={() => setOpenRun(null)} />;
  }

  const runs = overview?.runs || [];
  const active = runs.filter((r) => ["queued", "running", "paused"].includes(r.status));
  const recent = runs.filter((r) => !active.includes(r));
  const sets = overview?.sets || [];
  const hardMax = overview?.hard_max_iterations || 10;
  const selected = sets.find((item) => item.set_id === promptSetId);

  async function start() {
    if (!question.trim() && !testSetId && !moreQuestions.trim()) {
      setError("Type a test question, or pick a test set.");
      return;
    }
    setStarting(true);
    setError("");
    try {
      const extra = moreQuestions
        .split("\n")
        .map((row) => row.trim())
        .filter(Boolean);
      const created = await startLabRun({
        question: question.trim() || undefined,
        questions: extra,
        test_set_id: testSetId || undefined,
        prompt_set_id: promptSetId || undefined,
        max_iterations: maxIter,
      });
      setQuestion("");
      setMoreQuestions("");
      setOpenRun(created.run_id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start.");
    } finally {
      setStarting(false);
    }
  }

  return (
    <div className="lab-shell">
      <Segmented
        ariaLabel="Prompt Lab"
        value={tab}
        onChange={setTab}
        options={[
          { id: "lab", label: "Control room" },
          { id: "library", label: "Edit prompts" },
        ]}
      />
      {error ? <p className="desk-error">{error}</p> : null}
      <ApiRoster />

      {tab === "library" ? (
        <LabLibrary
          sets={sets}
          testSets={overview?.test_sets || []}
          onRefresh={() => void refresh()}
        />
      ) : (
        <div className="lab-dashboard">
          <header className="lab-dash-head">
            <div>
              <p className="lab-kicker">Prompt Lab</p>
              <h2>Watch Primer improve itself</h2>
            </div>
          </header>

          <form
            className="lab-compose"
            onSubmit={(e) => {
              e.preventDefault();
              void start();
            }}
          >
            <label className="lab-compose-label" htmlFor="lab-question">
              Test question
            </label>
            <Field
              id="lab-question"
              rows={4}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Why does calculus use limits?"
            />
            <label className="lab-compose-label" htmlFor="lab-extra">
              Extra questions (optional, one per line)
            </label>
            <Field
              id="lab-extra"
              rows={3}
              value={moreQuestions}
              onChange={(e) => setMoreQuestions(e.target.value)}
              placeholder={"What is a derivative?\nWhy is integration useful?"}
            />
            <div className="lab-compose-row">
              <label>
                Test set
                <select value={testSetId} onChange={(e) => setTestSetId(e.target.value)}>
                  <option value="">None</option>
                  {(overview?.test_sets || []).map((item) => (
                    <option key={item.set_id} value={item.set_id}>
                      {item.name} ({item.questions.length})
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Max iterations
                <select value={maxIter} onChange={(e) => setMaxIter(Number(e.target.value))}>
                  {Array.from({ length: hardMax }, (_, i) => i + 1).map((n) => (
                    <option key={n} value={n}>
                      {n}
                      {n === 5 ? " (default)" : ""}
                    </option>
                  ))}
                </select>
              </label>
              <Button type="submit" disabled={starting}>
                {starting ? "Starting…" : "Start evolution"}
              </Button>
            </div>
            <p className="lab-muted">
              {selected
                ? `Will use ${selected.topic} v${selected.active_version}${selected.is_generic ? " (generic fallback)" : ""}.`
                : "Selector will pick a topic prompt, or Generic if nothing matches."}{" "}
              The run continues if you leave this page.
            </p>
          </form>
          {error ? <p className="desk-error lab-compose-error">{error}</p> : null}

          <p className="lab-kicker">Prompt library</p>
          <p className="lab-muted lab-lib-lead">
            These Director prompts already exist. Click one to force it on the next run, or open{" "}
            <button type="button" className="lab-inline" onClick={() => setTab("library")}>
              Edit prompts
            </button>{" "}
            to version the text.
          </p>
          <div className="lab-grid">
            {sets.length ? (
              sets.map((item) => (
                <button
                  type="button"
                  key={item.set_id}
                  className={`lab-card lab-set-card ${promptSetId === item.set_id ? "is-on" : ""}`}
                  onClick={() => setPromptSetId((id) => (id === item.set_id ? "" : item.set_id))}
                >
                  <p className="lab-kicker">
                    {item.is_generic ? "Fallback" : item.prompt_type} · v{item.active_version}
                  </p>
                  <h3>{item.topic}</h3>
                  <p>{item.description}</p>
                  <p className="lab-meta">
                    {item.best_score ? `${item.best_score.toFixed(1)} best · ` : ""}
                    {item.run_count} runs
                  </p>
                </button>
              ))
            ) : (
              <p className="lab-muted">
                {error
                  ? "Library did not load. Restart the Primer backend, then click Prompt Lab again."
                  : "Loading Calculus, Finance, Economics, Technology, Physics, Biology, and Generic…"}
              </p>
            )}
          </div>

          <p className="lab-kicker">Active</p>
          <div className="lab-grid">
            {active.length ? (
              active.map((run) => <RunCard key={run.run_id} run={run} onOpen={() => setOpenRun(run.run_id)} />)
            ) : (
              <p className="lab-muted">No experiments in flight yet.</p>
            )}
          </div>
          <p className="lab-kicker">Recent</p>
          <div className="lab-grid">
            {recent.length ? (
              recent.map((run) => <RunCard key={run.run_id} run={run} onOpen={() => setOpenRun(run.run_id)} />)
            ) : (
              <p className="lab-muted">Completed runs will land here.</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
