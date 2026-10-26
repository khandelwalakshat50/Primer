# Primer

Primer turns a question, or a whole topic, into short explainer reels that play inside an iPhone frame in the browser. Each beat is narration plus a flat SVG diagram: one idea at a time, drawn on while the line is spoken.

A single question becomes one reel. A topic becomes a short course: a syllabus first, then one reel per lesson. There is also a prompt lab for editing the prompts that write those lessons and comparing runs.

The backend is FastAPI. The frontend is React and Vite. Keys stay on the server. The browser talks to `/api` and never sees them.

## Setup

You need Python 3.11+ and Node.js.

1. Copy the env template and add the keys you use. At least one of `GEMINI_API_KEY`, `OPENAI_API_KEY`, or `ANTHROPIC_API_KEY` is required for generation. Hindi narration needs `ELEVENLABS_API_KEY`. English narration can use ElevenLabs or `DEEPGRAM_API_KEY`.

```bash
cp .env.example .env
```

2. Backend, port **8005**:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --reload-exclude '.venv/*' --port 8005
```

Reels, courses, and video exports run as server-side jobs. They keep going if the browser closes, and a restart resumes unfinished work. Video export uses `imageio-ffmpeg` and `resvg-py` from `requirements.txt`. No system ffmpeg is required.

3. Frontend, port **5180**:

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:5180](http://localhost:5180). Vite proxies `/api` to the backend.

## What stays off GitHub

`.gitignore` keeps local secrets and generated files out of commits:

- `.env` and any `.env.*` file except `.env.example`
- Python virtualenvs (`backend/.venv`, `backend/.venv.broken`) and `frontend/node_modules`
- Generated data under `backend/data/` (audio cache, mp4 exports, `primer.db`, curricula, reel jobs) and `eval_out/`

`.env.example` lists every variable the server reads, with placeholders only. Put real keys in `.env`. Before the first commit, run `git status` and confirm `.env` is not listed.

`SYSTEM.md` is the design spec for how courses and reels are built. `ANIMATION.md` is a note on motion in the player.
