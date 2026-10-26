"""Dump Director JSON + SVGs for visual review.

  cd backend && ../.venv/bin/python eval_reels.py

Uses canned answers so you can inspect copy/alignment without waiting on the chat stream.
Set SKIP_LLM=1 to only write fixture SVGs through the fitter (no API).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from director import plan_reel
from svg_fit import fit_svg, normalize_reel_svg

SAMPLES = [
    (
        "Why does ice float?",
        "Ice floats because the open hexagonal lattice makes ice about 9% less dense than liquid water. "
        "Hydrogen bonds hold water molecules farther apart in the crystal than in the liquid, so a chunk of ice occupies more volume per gram and the denser liquid pushes it up.",
        "Why Ice Floats",
    ),
    (
        "What happens to bond prices when interest rates rise?",
        "When market interest rates rise, newly issued bonds pay a higher coupon. An old bond with a smaller coupon looks worse, so its market price falls until its yield matches the new rate. Price and yield move in opposite directions.",
        "Bond Prices",
    ),
    (
        "Why is the sky blue?",
        "Rayleigh scattering sends shorter blue wavelengths of sunlight out of the direct beam more than red. Looking away from the sun, those scattered blue photons reach your eye. At sunset the path is longer, so blue is scattered out and red remains.",
        "Sky Color",
    ),
    (
        "What is photosynthesis?",
        "Photosynthesis uses chlorophyll to catch photons and split water. The plant stores energy as sugar and releases oxygen as a leftover from the water-splitting step.",
        "Photosynthesis",
    ),
    (
        "Why do we have seasons?",
        "Earth's axis is tilted about 23.5 degrees. In June the northern hemisphere leans toward the sun, so rays hit more directly and days are longer. That extra energy is summer, not a change in distance from the sun.",
        "Seasons",
    ),
    (
        "How does a vaccine work?",
        "A vaccine shows the immune system a harmless piece of a pathogen, often a spike protein. Memory B cells and T cells remain. Later infection meets antibodies already trained on that protein.",
        "Vaccines",
    ),
    (
        "What is inflation?",
        "Inflation is a sustained rise in the general price level, often measured by CPI. If the money supply and demand outrun real goods, each dollar buys fewer groceries.",
        "Inflation",
    ),
    (
        "Why does a metal spoon feel colder than a wooden one?",
        "Both start at room temperature. Metal has high thermal conductivity, so it pulls heat from your skin fast and you feel cold. Wood conducts poorly, so your skin stays warmer.",
        "Conductivity",
    ),
]

OVERFLOW = """<svg viewBox="0 0 360 300">
  <rect x="-80" y="-20" width="520" height="400" fill="#d7e8f8"/>
  <text x="8" y="20" font-size="14">old bond</text>
</svg>"""


def _write_svgs(folder: Path, reel: dict) -> None:
    idx = 0
    for beat in [reel["hook"], *reel["scenes"], reel["payoff"]]:
        for item in beat.get("on_screen") or []:
            if item.get("type") != "svg" or not item.get("content"):
                continue
            path = folder / f"{idx:02d}_{beat['scene_id']}.svg"
            path.write_text(item["content"], encoding="utf-8")
            idx += 1


def main() -> None:
    out = ROOT / "eval_out"
    out.mkdir(exist_ok=True)
    fit_demo = out / "_fit_demo"
    fit_demo.mkdir(exist_ok=True)
    (fit_demo / "before.svg").write_text(OVERFLOW, encoding="utf-8")
    (fit_demo / "after.svg").write_text(normalize_reel_svg(OVERFLOW) or fit_svg(OVERFLOW), encoding="utf-8")

    if os.getenv("SKIP_LLM") == "1":
        print(f"Wrote fitter demo to {fit_demo}. Set SKIP_LLM=0 to run the Director.")
        return

    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    openai_model = os.getenv("DIRECTOR_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4.1"
    gemini_model = os.getenv("DIRECTOR_GEMINI_MODEL") or "gemini-2.5-pro"

    for i, (question, answer, title) in enumerate(SAMPLES, start=1):
        slug = f"{i:02d}_{title.lower().replace(' ', '_')}"
        folder = out / slug
        folder.mkdir(exist_ok=True)
        print(f"Planning {slug}...")
        reel = plan_reel(
            question,
            answer,
            title,
            openai_key,
            gemini_key,
            openai_model,
            gemini_model,
        )
        (folder / "reel.json").write_text(json.dumps(reel, indent=2, ensure_ascii=False), encoding="utf-8")
        (folder / "source.md").write_text(f"# {question}\n\n{answer}\n", encoding="utf-8")
        if reel:
            _write_svgs(folder, reel)
        print(f"  -> {folder}")


if __name__ == "__main__":
    main()
