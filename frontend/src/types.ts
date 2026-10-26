export type Role = "user" | "assistant";

export type Turn = {
  role: Role;
  content: string;
};

export type NotebookPage = {
  id: string;
  title: string;
  text: string;
  svgs: string[];
};

export type ReelElement = {
  type: "text" | "stat" | "arrow" | "svg";
  content: string;
  emphasis: "high" | "medium" | "low";
  reveal: number;
  direction: string;
  place?: "top" | "bottom" | "left" | "right" | "center" | "";
};

export type BeatVisual = {
  type?: string;
  sees?: string;
  keeps?: string;
  changes?: string;
  focus?: string;
  motion?: string;
  emphasis?: "quiet" | "normal" | "loud";
  medium?: "svg" | "sora" | "image"; // future: generated_clip beside svg
};

export type BeatVideo = {
  id: string;
  url: string;
  model: string;
  seconds: number;
  size: string;
};

export type BeatImage = {
  id: string;
  url: string;
  model: string;
  size: string;
  mime?: string;
};

export type ReelBeat = {
  scene_id: string;
  purpose: string;
  narration: string;
  bg: string;
  on_screen: ReelElement[];
  visual?: BeatVisual;
  media?: "svg" | "sora" | "image";
  video?: BeatVideo | null;
  image?: BeatImage | null;
};

export type StepMetric = {
  step: string;
  label: string;
  provider: string;
  model: string;
  seconds: number;
  input_tokens: number;
  output_tokens: number;
  calls: number;
  cost_usd: number | null;
  ok: boolean;
};

export type ReelMetrics = {
  steps: StepMetric[];
  llm_seconds?: number;
  scripted_seconds?: number;
  directed_seconds?: number;
  input_tokens?: number;
  output_tokens?: number;
  calls?: number;
  cost_usd?: number | null;
  unpriced_models?: string[];
};

export type ReelPlan = {
  title: string;
  hook: ReelBeat;
  scenes: ReelBeat[];
  payoff: ReelBeat;
  script_model?: string;
  svg_model?: string;
  visual_engine?: "svg" | "sora";
  video?: {
    id: string;
    url: string;
    model: string;
    seconds: number;
    size: string;
  } | null;
  metrics?: ReelMetrics;
};

export type ChatReply = {
  topicTitle: string;
  isNewTopic: boolean;
  handwriting: string;
  svg?: string;
  reel?: ReelPlan;
};

export type ReelSpecStatus = "pending" | "scripted" | "generating" | "ready" | "failed";

export type CurriculumReelSpec = {
  reel_id: string;
  title: string;
  objective: string;
  key_terms: string[];
  depends_on: string[];
  difficulty: number;
  status: ReelSpecStatus;
  attempt: number;
  error: string;
  narration_draft: string;
  coherency_notes: string[];
  visual_world?: string;
  metrics?: ReelMetrics;
  reel: ReelPlan | null;
  stage?: string;
  stage_label?: string;
  progress?: number;
  started_at?: number;
  updated_at?: number;
};

export type CurriculumStatus = "planning" | "generating" | "ready" | "failed";

export type Curriculum = {
  curriculum_id: string;
  topic: string;
  created_at: number;
  reels: CurriculumReelSpec[];
  status: CurriculumStatus;
  spine?: string;
  source_text?: string;
  /** Why the course is not moving right now, e.g. queued behind another. */
  note?: string;
};

export type JobStatus = "queued" | "running" | "ready" | "failed" | "stopped";

/** A single reel being made on the server. Its id is also its lesson id. */
export type ReelJob = {
  job_id: string;
  created_at: number;
  updated_at: number;
  status: JobStatus;
  stage: string;
  stage_label: string;
  progress: number;
  question: string;
  topic_title: string;
  is_new_topic: boolean;
  handwriting: string;
  reel: ReelPlan | null;
  error: string;
  visual_engine?: "svg" | "sora";
  /** Set by the client when the server has no record (an old, interrupted reel). */
  missing?: boolean;
};

export type ExportJob = {
  export_id: string;
  title: string;
  filename: string;
  status: "queued" | "rendering" | "encoding" | "done" | "failed";
  progress: number;
  stage_label: string;
  error: string;
  size_bytes: number;
};

export type CachedLesson = {
  id: string;
  kind: "reel" | "course";
  question: string;
  title: string;
  handwriting: string;
  reel: ReelPlan | null;
  curriculum?: Curriculum;
  createdAt: number;
};
