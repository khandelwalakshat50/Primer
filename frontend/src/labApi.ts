import type {
  EvolutionRun,
  LabOverview,
  PromptDiff,
  PromptSet,
  RunSummary,
  TestSet,
} from "./labTypes";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") {
        detail = body.detail;
      } else if (Array.isArray(body.detail)) {
        detail = body.detail.map((row: { msg?: string }) => row.msg || JSON.stringify(row)).join("; ");
      }
      if (res.status === 404 && !body.detail) {
        detail = "Prompt Lab API is missing. Restart the Primer backend on port 8005.";
      }
    } catch {
      if (res.status === 404) {
        detail = "Prompt Lab API is missing. Restart the Primer backend on port 8005.";
      }
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export async function fetchLabOverview(): Promise<LabOverview> {
  return json(await fetch("/api/prompt-lab/overview"));
}

export async function fetchLabRun(runId: string): Promise<EvolutionRun> {
  return json(await fetch(`/api/prompt-lab/runs/${runId}`));
}

export async function startLabRun(payload: {
  question?: string;
  questions?: string[];
  test_set_id?: string;
  prompt_set_id?: string;
  max_iterations: number;
}): Promise<RunSummary> {
  return json(
    await fetch("/api/prompt-lab/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
}

export async function pauseLabRun(runId: string): Promise<RunSummary> {
  return json(await fetch(`/api/prompt-lab/runs/${runId}/pause`, { method: "POST" }));
}

export async function resumeLabRun(runId: string): Promise<RunSummary> {
  return json(await fetch(`/api/prompt-lab/runs/${runId}/resume`, { method: "POST" }));
}

export async function stopLabRun(runId: string): Promise<RunSummary> {
  return json(await fetch(`/api/prompt-lab/runs/${runId}/stop`, { method: "POST" }));
}

export async function retryLabRun(runId: string): Promise<RunSummary> {
  return json(await fetch(`/api/prompt-lab/runs/${runId}/retry`, { method: "POST" }));
}

export async function fetchLabDiff(runId: string, a: number, b: number): Promise<PromptDiff> {
  return json(await fetch(`/api/prompt-lab/runs/${runId}/diff?a=${a}&b=${b}`));
}

export async function createPromptSet(payload: {
  topic: string;
  prompt: string;
  description?: string;
  notes?: string;
  tags?: string[];
}): Promise<PromptSet> {
  return json(
    await fetch("/api/prompt-lab/prompts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
}

export async function patchPromptSet(
  setId: string,
  payload: Partial<{ topic: string; description: string; notes: string; active: boolean; tags: string[] }>,
): Promise<PromptSet> {
  return json(
    await fetch(`/api/prompt-lab/prompts/${setId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
}

export async function deletePromptSet(setId: string): Promise<void> {
  await json(await fetch(`/api/prompt-lab/prompts/${setId}`, { method: "DELETE" }));
}

export async function addPromptVersion(setId: string, prompt: string, reason: string): Promise<void> {
  await json(
    await fetch(`/api/prompt-lab/prompts/${setId}/versions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, reason }),
    }),
  );
}

export async function activatePromptVersion(setId: string, version: number): Promise<PromptSet> {
  return json(await fetch(`/api/prompt-lab/prompts/${setId}/activate/${version}`, { method: "POST" }));
}

export async function promoteFromRun(setId: string, version: number): Promise<PromptSet> {
  return json(
    await fetch("/api/prompt-lab/promote", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ set_id: setId, version }),
    }),
  );
}

export async function createTestSet(payload: {
  name: string;
  topic?: string;
  questions: string[];
}): Promise<TestSet> {
  return json(
    await fetch("/api/prompt-lab/test-sets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
}
