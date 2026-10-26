import { useEffect, useState } from "react";
import { fetchPrimerModels, type PrimerModels } from "../api";

function line(
  primaryOn: boolean,
  primaryApi: string,
  primaryModel: string,
  fallbackOn: boolean,
  fallbackApi: string,
  fallbackModel: string,
) {
  if (primaryOn && fallbackOn) {
    return `${primaryApi} ${primaryModel} → ${fallbackApi} ${fallbackModel} if that fails`;
  }
  if (primaryOn) return `${primaryApi} ${primaryModel}`;
  if (fallbackOn) return `${fallbackApi} ${fallbackModel}`;
  return "No API key configured";
}

export function ApiRoster() {
  const [models, setModels] = useState<PrimerModels | null>(null);

  useEffect(() => {
    void fetchPrimerModels().then(setModels);
  }, []);

  if (!models) {
    return (
      <section className="lab-api">
        <p className="lab-kicker">What each API does</p>
        <p className="lab-muted">Loading model roster…</p>
      </section>
    );
  }

  const script = [
    line(
      models.has_openai_key,
      "OpenAI",
      models.openai_model,
      models.has_gemini_key,
      "Gemini",
      models.gemini_model,
    ),
    models.has_claude_key ? `Claude ${models.claude_model || "claude-opus-5"}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  const director = line(
    models.has_openai_key,
    "OpenAI",
    models.director_model || models.openai_model,
    models.has_gemini_key,
    "Gemini",
    models.director_gemini_model || models.gemini_model,
  );

  const rows = [
    {
      job: "Chat answers",
      does: "Replies in the notebook chat when you ask a question.",
      who: script,
    },
    {
      job: "Live web briefing",
      does: "When you ask for latest news or current events, Primer searches the web (Gemini Google Search, then OpenAI) and hands those facts to the tutor so it does not invent headlines.",
      who: models.has_gemini_key
        ? `Gemini ${models.gemini_model} search`
        : models.has_openai_key
          ? `OpenAI ${models.openai_model} search`
          : "No API key configured",
    },
    {
      job: "Scripts & coherency",
      does: "Writes the lesson narration and keeps later lessons consistent with earlier ones.",
      who: script,
    },
    {
      job: "Curriculum planner",
      does: "Breaks a topic into the syllabus before any script exists.",
      who: director,
    },
    {
      job: "Director & SVG",
      does: "Cuts the script into beats and draws the diagrams.",
      who: director,
    },
    {
      job: "Sora 2 + stills",
      does: "When Sora 2 is on, the reel opens on one Sora clip, then two OpenAI stills, with SVG for the rest. Type is overlaid on video and photos.",
      who: models.has_sora_key
        ? `OpenAI ${models.sora_model || "sora-2"}`
        : "SORA_API_KEY missing",
    },
    {
      job: "Prompt Lab critic",
      does: "Scores a generated reel (teaching, continuity, pacing, and the rest).",
      who: director,
    },
    {
      job: "Prompt Lab optimizer",
      does: "Proposes a targeted edit to the Director prompt.",
      who: director,
    },
    {
      job: "Voice / narration",
      does: "Speaks each beat. English and Hindi use ElevenLabs when the key is set; Deepgram is the English fallback.",
      who: [
        models.has_elevenlabs_key
          ? "ElevenLabs English + Hindi"
          : "ElevenLabs key missing",
        models.has_deepgram_key
          ? `Deepgram ${models.deepgram_tts_model} fallback`
          : "Deepgram key missing",
      ].join(" · "),
    },
    {
      job: "Prompt Lab topic pick",
      does: "Chooses Calculus vs Finance vs Generic when you hit Start.",
      who: "Keyword match — no API call",
    },
  ];

  return (
    <section className="lab-api">
      <p className="lab-kicker">What each API does</p>
      <p className="lab-muted">
        Voice is ElevenLabs (energetic English, multilingual Hindi). Deepgram is only the English fallback.
      </p>
      <ul className="lab-api-list">
        {rows.map((row) => (
          <li key={row.job}>
            <p className="lab-api-job">{row.job}</p>
            <p className="lab-api-who">{row.who}</p>
            <p className="lab-api-does">{row.does}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}
