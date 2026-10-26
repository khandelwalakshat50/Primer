import { useLayoutEffect, useMemo, useRef, useState } from "react";
import type { NotebookPage } from "../types";
import type { PageMetrics } from "../paginate";

type Props = {
  page: NotebookPage;
  side: "single" | "left" | "right";
  animateFrom?: string;
  emptyHint?: string;
  onMetrics?: (metrics: PageMetrics) => void;
};

export function PageSheet({
  page,
  side,
  animateFrom = "",
  emptyHint,
  onMetrics,
}: Props) {
  const [shown, setShown] = useState(page.text);
  const sheetRef = useRef<HTMLElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    if (!onMetrics) return;
    const sheet = sheetRef.current;
    const body = bodyRef.current;
    if (!sheet || !body) return;

    const report = () => {
      const padBottom = parseFloat(getComputedStyle(sheet).paddingBottom) || 0;
      const available =
        sheet.clientHeight - (body.offsetTop - sheet.offsetTop) - padBottom;
      if (body.clientWidth > 0 && available > 80) {
        onMetrics({ width: body.clientWidth, maxHeight: available });
      }
    };

    report();
    const observer = new ResizeObserver(report);
    observer.observe(sheet);
    return () => observer.disconnect();
  }, [onMetrics]);

  useLayoutEffect(() => {
    if (page.text === animateFrom) {
      setShown(page.text);
      return;
    }

    const prefix = page.text.startsWith(animateFrom) ? animateFrom : "";
    const addition = page.text.slice(prefix.length);
    setShown(prefix);
    if (!addition) return;

    let i = 0;
    const step = () => {
      i = Math.min(addition.length, i + (addition[i] === " " ? 1 : 2));
      setShown(prefix + addition.slice(0, i));
      if (i < addition.length) id = window.setTimeout(step, 14);
    };
    let id = window.setTimeout(step, 14);
    return () => window.clearTimeout(id);
  }, [page.text, animateFrom]);

  const paragraphs = useMemo(
    () => shown.split(/\n+/).filter((p) => p.length > 0),
    [shown],
  );

  return (
    <article ref={sheetRef} className={`sheet sheet-${side}`}>
      <div className="sheet-holes" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
      <h2 className="sheet-title">{page.title}</h2>
      <div className="sheet-body" ref={bodyRef}>
        {paragraphs.length === 0 ? (
          <p className="sheet-hint">{emptyHint}</p>
        ) : (
          paragraphs.map((p, i) => <p key={`${i}-${p.slice(0, 12)}`}>{p}</p>)
        )}
      </div>
      {page.svgs.length > 0 ? (
        <div className="sheet-figures">
          {page.svgs.map((svg, index) => (
            <div
              key={`${page.id}-svg-${index}`}
              className="sheet-figure"
              dangerouslySetInnerHTML={{ __html: svg }}
            />
          ))}
        </div>
      ) : null}
    </article>
  );
}
