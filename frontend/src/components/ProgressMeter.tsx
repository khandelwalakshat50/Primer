import { useEffect, useState, type ReactNode } from "react";

/** Rough time left from how fast progress has moved since we first saw it. */
function useEta(progress: number, key: string): number | null {
  const [now, setNow] = useState(() => Date.now());
  const [origin, setOrigin] = useState(() => ({ key, at: now, p: progress }));

  // Restart the clock when the thing being measured changes.
  if (origin.key !== key || progress < origin.p) {
    setOrigin({ key, at: now, p: progress });
  }

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const elapsed = (now - origin.at) / 1000;
  const moved = progress - origin.p;
  if (progress >= 1 || elapsed < 8 || moved < 0.04) return null;
  return (elapsed / moved) * (1 - progress);
}

function formatEta(seconds: number | null): string {
  if (seconds == null || !Number.isFinite(seconds)) return "";
  if (seconds < 45) return "less than a minute left";
  const minutes = Math.round(seconds / 60);
  return minutes <= 1 ? "about a minute left" : `about ${minutes} min left`;
}

type MeterProps = {
  progress: number;
  label: string;
  /** Identifies the thing being measured so the ETA restarts when it changes. */
  etaKey?: string;
  indeterminate?: boolean;
  tone?: "default" | "danger";
  compact?: boolean;
};

export function ProgressMeter({
  progress,
  label,
  etaKey,
  indeterminate = false,
  tone = "default",
  compact = false,
}: MeterProps) {
  const clamped = Math.max(0, Math.min(1, progress || 0));
  const eta = useEta(clamped, etaKey ?? "");
  const pct = Math.round(clamped * 100);
  const etaText = etaKey && !indeterminate ? formatEta(eta) : "";
  return (
    <div className={`meter${compact ? " is-compact" : ""} is-${tone}`}>
      <div className="meter-head">
        <span className="meter-label">{label}</span>
        {indeterminate ? null : <span className="meter-pct">{pct}%</span>}
      </div>
      <div
        className={`meter-track${indeterminate ? " is-indeterminate" : ""}`}
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={indeterminate ? undefined : pct}
      >
        <div className="meter-fill" style={{ width: indeterminate ? undefined : `${pct}%` }} />
      </div>
      {etaText && !compact ? <p className="meter-eta">{etaText}</p> : null}
    </div>
  );
}

const STEPS = [
  { id: "script", label: "Script" },
  { id: "storyboard", label: "Storyboard" },
  { id: "drawing", label: "Drawing" },
  { id: "ready", label: "Ready" },
] as const;

function stepIndex(stage: string): number {
  if (stage === "ready") return 3;
  if (stage === "drawing" || stage === "finishing") return 2;
  if (stage === "storyboard" || stage === "waiting") return 1;
  return 0;
}

/** The four phases a reel goes through, with the current one lit. */
export function StageSteps({ stage }: { stage: string }) {
  const at = stepIndex(stage);
  const idle = stage === "queued" || stage === "" || stage === "pending";
  return (
    <ol className="stage-steps">
      {STEPS.map((step, i) => {
        const state = idle ? "todo" : i < at ? "done" : i === at ? "now" : "todo";
        return (
          <li key={step.id} className={`stage-step is-${state}`}>
            <span className="stage-dot" aria-hidden="true">
              {state === "done" ? "✓" : ""}
            </span>
            {step.label}
          </li>
        );
      })}
    </ol>
  );
}

type CardProps = {
  title: string;
  subtitle?: string;
  stage: string;
  label: string;
  progress: number;
  etaKey: string;
  note?: string;
  indeterminate?: boolean;
  children?: ReactNode;
};

/** What the learner sees on the phone while a reel is being made. */
export function GenerationCard({
  title,
  subtitle,
  stage,
  label,
  progress,
  etaKey,
  note,
  indeterminate = false,
  children,
}: CardProps) {
  return (
    <div className="gen-card" aria-live="polite">
      <p className="gen-kicker">{subtitle || "Making your reel"}</p>
      <p className="gen-title">{title}</p>
      {indeterminate ? null : <StageSteps stage={stage} />}
      <ProgressMeter
        progress={progress}
        label={label}
        etaKey={etaKey}
        indeterminate={indeterminate}
      />
      {note ? <p className="gen-note">{note}</p> : null}
      <p className="gen-hint">
        This runs on the server — you can switch lessons or close the tab and come back.
      </p>
      {children ? <div className="gen-actions">{children}</div> : null}
    </div>
  );
}
