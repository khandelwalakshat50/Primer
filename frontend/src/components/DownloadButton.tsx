import { useEffect, useRef, useState } from "react";
import { exportFileUrl, fetchExport, lookupExport, startExport, type VoiceLang } from "../api";
import type { ExportJob, ReelPlan } from "../types";

type Props = {
  title: string;
  reels: ReelPlan[];
  lang: VoiceLang;
  label?: string;
  variant?: "primary" | "ghost";
  /** Start building the video on open if none is stored yet (cheap for one lesson). */
  prepare?: boolean;
};

const POLL_MS = 1000;

function fileName(title: string): string {
  const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 60).replace(/^-+|-+$/g, "");
  return `${slug || "primer-reel"}.mp4`;
}

function saveFile(job: ExportJob, title: string) {
  const name = fileName(title);
  const a = document.createElement("a");
  a.href = exportFileUrl(job.export_id, name);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function megabytes(bytes: number): string {
  return bytes ? `${(bytes / 1_000_000).toFixed(1)} MB` : "";
}

/**
 * Videos are built on the server (automatically when a reel is ready) and kept
 * on disk. This finds the stored file, shows progress while one is being made,
 * and saves it — instantly when it already exists.
 */
export function DownloadButton({
  title,
  reels,
  lang,
  label = "Download video",
  variant = "ghost",
  prepare = true,
}: Props) {
  const [job, setJob] = useState<ExportJob | null>(null);
  const [error, setError] = useState("");
  const [checking, setChecking] = useState(true);
  /** The learner asked for the file; save it the moment it is ready. */
  const [wanted, setWanted] = useState(false);
  const alive = useRef(true);
  const reelsRef = useRef(reels);
  reelsRef.current = reels;

  useEffect(() => {
    alive.current = true;
    void lookupExport({ reels: reelsRef.current, lang, title, prepare })
      .then((found) => {
        if (alive.current) setJob(found);
      })
      .catch(() => undefined)
      .finally(() => {
        if (alive.current) setChecking(false);
      });
    return () => {
      alive.current = false;
    };
    // The parent keys this component by lesson + language.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const working = job !== null && job.status !== "done" && job.status !== "failed";

  useEffect(() => {
    if (!job || !working) return;
    const id = window.setTimeout(async () => {
      try {
        const next = await fetchExport(job.export_id);
        if (!alive.current) return;
        setJob(next);
        if (next.status === "done" && wanted) {
          saveFile(next, title);
          setWanted(false);
        }
        if (next.status === "failed") setError(next.error || "The export failed.");
      } catch (err) {
        if (!alive.current) return;
        setError(err instanceof Error ? err.message : "Lost track of the export.");
        setJob(null);
      }
    }, POLL_MS);
    return () => window.clearTimeout(id);
  }, [job, working, wanted, title]);

  async function begin() {
    setError("");
    if (job?.status === "done") {
      saveFile(job, title);
      return;
    }
    setWanted(true);
    if (working) return;
    try {
      const started = await startExport({ title, reels, lang });
      if (!alive.current) return;
      setJob(started);
      if (started.status === "done") {
        saveFile(started, title);
        setWanted(false);
      }
    } catch (err) {
      setWanted(false);
      setError(err instanceof Error ? err.message : "Could not start the export.");
    }
  }

  if (working && job) {
    const pct = Math.round(job.progress * 100);
    return (
      <button
        type="button"
        className={`dl is-working${wanted ? " is-wanted" : ""}`}
        onClick={() => setWanted(true)}
        title={wanted ? "It will save as soon as it's ready" : "Click to save it when it's ready"}
      >
        <span className="dl-head">
          <span>{wanted ? "Saves when ready · " : "Preparing · "}{job.stage_label || "Queued"}</span>
          <span className="dl-pct">{pct}%</span>
        </span>
        <span className="meter-track">
          <span className="meter-fill" style={{ width: `${pct}%` }} />
        </span>
      </button>
    );
  }

  const ready = job?.status === "done";
  return (
    <div className="dl">
      <button
        type="button"
        className={`btn btn-${variant} dl-btn`}
        onClick={() => void begin()}
        disabled={!reels.length || checking}
        title={
          ready
            ? "Saved on the server — downloads instantly"
            : "Render an MP4 (1080×1920) with narration and captions"
        }
      >
        <DownloadIcon /> {error ? "Try the download again" : label}
        {ready ? <span className="dl-ready">Ready{job.size_bytes ? ` · ${megabytes(job.size_bytes)}` : ""}</span> : null}
      </button>
      {error ? <span className="dl-meta is-error">{error}</span> : null}
    </div>
  );
}

function DownloadIcon() {
  return (
    <svg className="dl-icon" viewBox="0 0 16 16" aria-hidden="true">
      <path d="M8 2v8m0 0 3.2-3.2M8 10 4.8 6.8M3 13h10" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
