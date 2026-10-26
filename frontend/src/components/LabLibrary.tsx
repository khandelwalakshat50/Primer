import { useEffect, useState } from "react";
import {
  activatePromptVersion,
  addPromptVersion,
  createPromptSet,
  createTestSet,
  deletePromptSet,
  patchPromptSet,
} from "../labApi";
import type { PromptSet, PromptVersion, TestSet } from "../labTypes";

export function LabLibrary({
  sets,
  testSets,
  onRefresh,
}: {
  sets: PromptSet[];
  testSets: TestSet[];
  onRefresh: () => void;
}) {
  const [openId, setOpenId] = useState<string | null>(null);
  const [topic, setTopic] = useState("");
  const [prompt, setPrompt] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState("");
  const [draft, setDraft] = useState("");
  const [reason, setReason] = useState("manual edit");
  const [tsName, setTsName] = useState("");
  const [tsQuestions, setTsQuestions] = useState("");
  const [expanded, setExpanded] = useState<number | null>(null);

  const open = sets.find((item) => item.set_id === openId) || null;
  const versions = open ? [...open.versions].sort((a, b) => b.version - a.version) : [];

  useEffect(() => {
    if (openId || !sets[0]) return;
    const item = sets[0];
    setOpenId(item.set_id);
    setExpanded(item.active_version);
    setDraft(item.versions.find((ver) => ver.version === item.active_version)?.prompt || "");
  }, [openId, sets]);

  function selectSet(item: PromptSet) {
    setOpenId(item.set_id);
    setExpanded(item.active_version);
    const current = item.versions.find((ver) => ver.version === item.active_version);
    setDraft(current?.prompt || "");
    setReason("manual edit");
    setError("");
  }

  async function addSet() {
    setError("");
    try {
      const created = await createPromptSet({
        topic,
        prompt,
        description,
        tags: [topic.toLowerCase()],
      });
      setTopic("");
      setPrompt("");
      setDescription("");
      setOpenId(created.set_id);
      setDraft(prompt);
      onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save.");
    }
  }

  async function saveVersion() {
    if (!open || !draft.trim()) return;
    setError("");
    try {
      await addPromptVersion(open.set_id, draft, reason);
      onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not version.");
    }
  }

  return (
    <div className="lab-library">
      {error ? <p className="desk-error">{error}</p> : null}

      <div className="lab-lib-layout">
        <aside className="lab-lib-nav">
          <p className="lab-kicker">Topics</p>
          {sets.map((item) => (
            <button
              type="button"
              key={item.set_id}
              className={`lab-lib-topic ${item.set_id === openId ? "is-on" : ""}`}
              onClick={() => selectSet(item)}
            >
              <span className="lab-lib-topic-name">{item.topic}</span>
              <span className="lab-meta">
                v{item.active_version}
                {item.is_generic ? " · fallback" : ""}
                {item.active ? "" : " · off"}
              </span>
            </button>
          ))}
        </aside>

        <section className="lab-lib-editor">
          {open ? (
            <>
              <header className="lab-lib-editor-head">
                <div>
                  <p className="lab-kicker">{open.is_generic ? "Generic fallback" : open.prompt_type}</p>
                  <h2>{open.topic}</h2>
                  <p className="lab-muted">{open.description || "No description yet."}</p>
                </div>
                <div className="lab-lib-actions">
                  <button
                    type="button"
                    className="lab-btn"
                    onClick={() => void patchPromptSet(open.set_id, { active: !open.active }).then(onRefresh)}
                  >
                    {open.active ? "Deactivate" : "Activate"}
                  </button>
                  {open.is_generic ? null : (
                    <button
                      type="button"
                      className="lab-btn"
                      onClick={() => {
                        if (window.confirm(`Delete ${open.topic}? Version history cannot be recovered.`)) {
                          void deletePromptSet(open.set_id).then(() => {
                            setOpenId(null);
                            onRefresh();
                          });
                        }
                      }}
                    >
                      Delete
                    </button>
                  )}
                </div>
              </header>

              <p className="lab-kicker">Versions</p>
              <ul className="lab-lib-versions">
                {versions.map((ver) => (
                  <VersionRow
                    key={ver.version_id}
                    ver={ver}
                    open={open}
                    expanded={expanded === ver.version}
                    onToggle={() => setExpanded((n) => (n === ver.version ? null : ver.version))}
                    onActivate={() => void activatePromptVersion(open.set_id, ver.version).then(onRefresh)}
                    onUseDraft={() => {
                      setDraft(ver.prompt);
                      setReason(`edit of v${ver.version}`);
                    }}
                  />
                ))}
              </ul>

              <p className="lab-kicker">New version</p>
              <p className="lab-muted">
                Saves as v{Math.max(0, ...open.versions.map((ver) => ver.version)) + 1}. Older versions stay.
              </p>
              <label className="lab-field">
                Why this change
                <input value={reason} onChange={(e) => setReason(e.target.value)} />
              </label>
              <label className="lab-field">
                Prompt
                <textarea rows={12} value={draft} onChange={(e) => setDraft(e.target.value)} />
              </label>
              <button type="button" className="lab-primary" onClick={() => void saveVersion()} disabled={!draft.trim()}>
                Save new version
              </button>
            </>
          ) : (
            <p className="lab-muted">Select a topic to inspect and version its Director prompt.</p>
          )}
        </section>
      </div>

      <div className="lab-lib-extras">
        <section className="lab-lib-editor">
          <p className="lab-kicker">Add a topic</p>
          <label className="lab-field">
            Topic
            <input value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="Psychology" />
          </label>
          <label className="lab-field">
            When to select it
            <input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Mind, behavior, experiments…" />
          </label>
          <label className="lab-field">
            Director prompt
            <textarea
              rows={6}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="Start from Generic, then add topic-specific visual rules."
            />
          </label>
          <button
            type="button"
            className="lab-primary"
            onClick={() => void addSet()}
            disabled={!topic.trim() || !prompt.trim()}
          >
            Add to library
          </button>
        </section>

        <section className="lab-lib-editor">
          <p className="lab-kicker">Test sets</p>
          <ul className="lab-lib-tests">
            {testSets.map((item) => (
              <li key={item.set_id}>
                <strong>{item.name}</strong>
                <span className="lab-meta"> · {item.questions.length} questions</span>
                <p className="lab-muted">{item.questions.join(" · ")}</p>
              </li>
            ))}
          </ul>
          <label className="lab-field">
            Name
            <input value={tsName} onChange={(e) => setTsName(e.target.value)} placeholder="Calculus extras" />
          </label>
          <label className="lab-field">
            Questions (one per line)
            <textarea rows={4} value={tsQuestions} onChange={(e) => setTsQuestions(e.target.value)} />
          </label>
          <button
            type="button"
            className="lab-primary"
            disabled={!tsName.trim() || !tsQuestions.trim()}
            onClick={() => {
              const questions = tsQuestions
                .split("\n")
                .map((row) => row.trim())
                .filter(Boolean);
              void createTestSet({ name: tsName, questions }).then(() => {
                setTsName("");
                setTsQuestions("");
                onRefresh();
              });
            }}
          >
            Add test set
          </button>
        </section>
      </div>
    </div>
  );
}

function VersionRow({
  ver,
  open,
  expanded,
  onToggle,
  onActivate,
  onUseDraft,
}: {
  ver: PromptVersion;
  open: PromptSet;
  expanded: boolean;
  onToggle: () => void;
  onActivate: () => void;
  onUseDraft: () => void;
}) {
  return (
    <li className={`lab-lib-ver ${expanded ? "is-open" : ""}`}>
      <div className="lab-lib-ver-top">
        <button type="button" className="lab-lib-ver-toggle" onClick={onToggle}>
          <strong>v{ver.version}</strong>
          {ver.version === open.active_version ? <span className="lab-badge">Active</span> : null}
          {ver.version === open.best_version && open.best_score ? (
            <span className="lab-badge">Best {open.best_score.toFixed(1)}</span>
          ) : null}
          <span className="lab-muted">{ver.reason || ver.diagnosis || "—"}</span>
        </button>
        <div className="lab-lib-ver-actions">
          {ver.version === open.active_version ? null : (
            <button type="button" className="lab-btn" onClick={onActivate}>
              Use as active
            </button>
          )}
          <button type="button" className="lab-btn" onClick={onUseDraft}>
            Edit from this
          </button>
        </div>
      </div>
      {expanded ? <pre className="lab-prompt is-compact">{ver.prompt}</pre> : null}
    </li>
  );
}
