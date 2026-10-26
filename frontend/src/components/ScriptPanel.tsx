import { useEffect, useMemo, useState } from "react";
import { Button } from "../ui";
import { plannedDurationMs } from "../timing";
import type { Curriculum, ReelBeat, ReelPlan } from "../types";

type Props = {
  reel: ReelPlan | null;
  curriculum: Curriculum | null;
  onClose: () => void;
};

function beatsOf(plan: ReelPlan): ReelBeat[] {
  return [plan.hook, ...plan.scenes, plan.payoff].filter(
    (beat) => beat.narration.trim() || beat.on_screen.length > 0,
  );
}

function headingOf(beat: ReelBeat): string {
  const item = beat.on_screen.find((i) => i.type === "text" || i.type === "stat");
  return item?.content ?? "";
}

function wordsOf(text: string): number {
  return text.trim().split(/\s+/).filter(Boolean).length;
}

function runtime(ms: number): string {
  const secs = Math.round(ms / 1000);
  if (secs < 60) return `${secs}s`;
  return `${Math.floor(secs / 60)}m ${String(secs % 60).padStart(2, "0")}s`;
}

type Section = { key: string; title: string; note: string; beats: ReelBeat[] };

function sectionsFrom(reel: ReelPlan | null, curriculum: Curriculum | null): Section[] {
  if (curriculum) {
    return curriculum.reels.map((spec, index) => ({
      key: spec.reel_id,
      title: `${index + 1}. ${spec.title}`,
      note:
        spec.status === "ready"
          ? spec.objective
          : spec.status === "failed"
            ? "This lesson could not be generated."
            : spec.status === "scripted"
              ? "Script ready — drawing the reel…"
            : spec.status === "generating"
              ? "Being written…"
              : "Not written yet.",
      beats: spec.reel ? beatsOf(spec.reel) : [],
    }));
  }
  if (reel) {
    return [{ key: "reel", title: reel.title, note: "", beats: beatsOf(reel) }];
  }
  return [];
}

function asPlainText(title: string, sections: Section[]): string {
  const lines: string[] = [title, ""];
  for (const section of sections) {
    if (sections.length > 1) {
      lines.push(section.title.toUpperCase());
      if (section.note) lines.push(section.note);
      lines.push("");
    }
    if (!section.beats.length) {
      lines.push("(no script yet)", "");
      continue;
    }
    section.beats.forEach((beat, i) => {
      const heading = headingOf(beat);
      lines.push(`${i + 1}. [${beat.purpose}] ${heading}`.trim());
      if (beat.narration.trim()) lines.push(`   ${beat.narration.trim()}`);
      lines.push("");
    });
  }
  return lines.join("\n").trim();
}

/** Clipboard API is refused in some contexts (denied permission, no user
 *  gesture). Fall back to a hidden selection so Copy is not a dead button. */
async function writeClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    /* fall through */
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;left:-9999px;top:0;";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  } catch {
    return false;
  }
}

export function ScriptPanel({ reel, curriculum, onClose }: Props) {
  const [copyState, setCopyState] = useState<"idle" | "done" | "failed">("idle");

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const sections = useMemo(() => sectionsFrom(reel, curriculum), [reel, curriculum]);
  const title = curriculum ? curriculum.topic : reel?.title || "Script";

  const totals = useMemo(() => {
    const beats = sections.flatMap((s) => s.beats);
    const words = beats.reduce((n, b) => n + wordsOf(b.narration), 0);
    return { beats: beats.length, words, ms: beats.reduce((n, b) => n + plannedDurationMs(b.narration), 0) };
  }, [sections]);

  async function copyAll() {
    const ok = await writeClipboard(asPlainText(title, sections));
    setCopyState(ok ? "done" : "failed");
    window.setTimeout(() => setCopyState("idle"), 1800);
  }

  return (
    <>
      <div className="script-backdrop" onClick={onClose} />
      <aside className="script-panel" role="dialog" aria-modal="true" aria-label="Reel script">
        <header className="script-head">
          <div>
            <p className="script-title">{title}</p>
            <p className="script-meta">
              {totals.beats} beat{totals.beats === 1 ? "" : "s"} · {totals.words} words · ~
              {runtime(totals.ms)} spoken
              {curriculum ? ` · ${curriculum.reels.length} lessons` : ""}
            </p>
          </div>
          <div className="script-head-actions">
            <Button variant="ghost" onClick={copyAll}>
              {copyState === "done" ? "Copied" : copyState === "failed" ? "Select & copy" : "Copy"}
            </Button>
            <Button variant="ghost" onClick={onClose} aria-label="Close script">
              Close
            </Button>
          </div>
        </header>

        <div className="script-body">
          {!sections.length ? (
            <p className="script-empty">No script yet — ask a question or build a course first.</p>
          ) : (
            sections.map((section) => (
              <section key={section.key} className="script-section">
                {sections.length > 1 ? (
                  <>
                    <h3 className="script-section-title">{section.title}</h3>
                    {section.note ? <p className="script-section-note">{section.note}</p> : null}
                  </>
                ) : null}
                {section.beats.length ? (
                  <ol className="script-beats">
                    {section.beats.map((beat, i) => (
                      <li key={`${section.key}-${i}`} className="script-beat">
                        <div className="script-beat-top">
                          <span className="script-role">{beat.purpose}</span>
                          <span className="script-heading">{headingOf(beat)}</span>
                        </div>
                        {beat.narration.trim() ? (
                          <p className="script-narration">{beat.narration.trim()}</p>
                        ) : null}
                        {beat.visual?.type ? (
                          <p className="script-visual">
                            {beat.visual.type}
                            {beat.visual.changes ? ` — ${beat.visual.changes}` : ""}
                          </p>
                        ) : null}
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="script-pending">{section.note || "No script yet."}</p>
                )}
              </section>
            ))
          )}
        </div>
      </aside>
    </>
  );
}
