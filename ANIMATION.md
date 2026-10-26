# Better video, still mostly SVG

Today each beat is a static SVG. The player only fades groups in and draws strokes. That is cheap. It also feels slow: stagger waits on each group, motion is not the idea (grow / split / travel), and later beats do not tween from the last frame.

## Same cost (player + prompts)

- Overlap reveals with speech. Cut `IDEAL_STROKE_MS` / stagger. Start the next group before the last stroke finishes.
- Motion by `visual.type`: stack = rise, split = peel apart, flow = travel along a path, grow = scale from the keep. Fade+draw is the fallback, not the default.
- GPU transforms (`translate` / `scale` / `opacity`), not layout. WAAPI or a small motion lib. Springs beat linear draw-on.
- Stable ids on recurring objects (`g#money_stack`). Morph keep-geometry between beats instead of a new fade-in of the same picture.
- Director already has `motion` / `changes` — have the SVG put those on `data-motion`. The player reads them. No new model.

## Little more cost

- One extra director field per group: enter / emphasize / exit. Tokens, not video bills.
- Ask the SVG model for interpolatable paths (same point count on keeps). Morph is still vector.
- Spend Sora (or a 2–4s clip) only on the loud transformation. Keep SVG for graphs, type, and labels.
- Optional: bake the 1–2 busiest beats to Lottie/Rive. Leave the rest live SVG.

## Skip for now

- Full-reel Sora / Kling / Manim / Remotion. Quality jump, 10–100× cost and wait.
- New raster every beat. Kills the “same object changes” trick and the pause/seek story.

## Do this first

1. Snappier, overlapping SVG motion in the player (no API spend).
2. Type-aware + morph-the-keeps.
3. One paid clip only where motion *is* the lesson.
