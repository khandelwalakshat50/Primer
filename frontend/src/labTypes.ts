import type { ReelPlan } from "./types";

export type LabScores = {
  teaching_quality: number;
  coherence: number;
  visual_continuity: number;
  entertainment: number;
  pacing: number;
  originality: number;
  visual_clarity: number;
  narration_quality: number;
  phone_readability: number;
  overall_score: number;
};

export type PromptVersion = {
  version_id: string;
  version: number;
  prompt: string;
  parent_version: number;
  reason: string;
  diagnosis: string;
  proposed_changes: string[];
  expected_impact: string;
  llm_model: string;
  run_id: string;
  test_question: string;
  critic_feedback: string;
  scores: Record<string, number>;
  created_at: number;
};

export type PromptSet = {
  set_id: string;
  topic: string;
  slug: string;
  description: string;
  prompt_type: string;
  active: boolean;
  is_generic: boolean;
  notes: string;
  tags: string[];
  active_version: number;
  best_version: number;
  best_score: number;
  run_count: number;
  created_at: number;
  updated_at: number;
  versions: PromptVersion[];
};

export type TestSet = {
  set_id: string;
  name: string;
  topic: string;
  questions: string[];
  created_at: number;
};

export type QuestionResult = {
  question: string;
  script: string;
  reel: ReelPlan | Record<string, unknown>;
  scores: Partial<LabScores>;
  strengths: string[];
  weaknesses: string[];
  failure_modes: string[];
  recommendations: string[];
  meaningful_remaining_improvement: boolean;
  remaining_improvement_evidence: string;
  error: string;
};

export type EvolutionIteration = {
  iteration: number;
  status: string;
  prompt_set_id: string;
  prompt_version: number;
  accepted: boolean;
  is_best: boolean;
  score: number;
  delta: number;
  scores: Partial<LabScores>;
  strengths: string[];
  weaknesses: string[];
  failure_modes: string[];
  recommendations: string[];
  questions: QuestionResult[];
  optimizer: Record<string, unknown>;
  error: string;
  retry_count: number;
  prompt_text: string;
  generation_start: number;
  generation_end: number;
  critic_start: number;
  critic_end: number;
  optimizer_start: number;
  optimizer_end: number;
};

export type RunSummary = {
  run_id: string;
  status: string;
  questions: string[];
  test_set_name: string;
  prompt_set_id: string;
  prompt_topic: string;
  selected_version: number;
  selection_reason: string;
  fallback: boolean;
  best_version: number;
  best_iteration: number;
  best_score: number;
  iteration_count: number;
  max_iterations: number;
  stop_reason: string;
  error: string;
  usage: Record<string, number>;
  created_at: number;
  updated_at: number;
  started_at: number;
  finished_at: number;
};

export type EvolutionRun = RunSummary & {
  current_version: number;
  improvement_threshold: number;
  stagnant_limit: number;
  stagnant_count: number;
  scripts: Record<string, string>;
  iterations: EvolutionIteration[];
  cancel_requested: boolean;
  pause_requested: boolean;
};

export type LabOverview = {
  hard_max_iterations: number;
  sets: PromptSet[];
  test_sets: TestSet[];
  runs: RunSummary[];
  active: RunSummary[];
};

export type DiffLine = { op: "added" | "removed" | "same"; text: string };

export type PromptDiff = {
  from_version: number;
  to_version: number;
  reason: string;
  diagnosis: string;
  expected_impact: string;
  proposed_changes: string[];
  lines: DiffLine[];
};
