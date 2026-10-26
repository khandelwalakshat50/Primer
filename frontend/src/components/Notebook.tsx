import type { PageMetrics } from "../paginate";
import type { NotebookPage } from "../types";
import { PageSheet } from "./PageSheet";

type Props = {
  spread: boolean;
  turning: boolean;
  left: NotebookPage;
  right?: NotebookPage;
  animateFrom: string;
  onMetrics: (metrics: PageMetrics) => void;
};

export function Notebook({
  spread,
  turning,
  left,
  right,
  animateFrom,
  onMetrics,
}: Props) {
  return (
    <div
      className={`notebook ${spread ? "is-spread" : "is-single"} ${turning ? "is-turning" : ""}`}
    >
      <div className="notebook-stage">
        {spread && right ? (
          <>
            <PageSheet page={left} side="left" animateFrom={left.text} />
            <div className="notebook-gutter" aria-hidden="true" />
            <PageSheet
              key={right.id}
              page={right}
              side="right"
              animateFrom={animateFrom}
              onMetrics={onMetrics}
            />
          </>
        ) : (
          <PageSheet
            page={left}
            side="single"
            animateFrom={animateFrom}
            emptyHint="The page is blank. Ask something, and I will write it here."
            onMetrics={onMetrics}
          />
        )}
      </div>
    </div>
  );
}
