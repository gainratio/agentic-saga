import { type SyntheticEvent, useEffect, useMemo, useState } from "react";
import { projectReplay } from "../replay/project-replay";
import { LIVE_TITLE, LiveExplainer } from "./live-explainer";
import styles from "./live-explainer.module.css";
import { RecorderWorkbench, type RecorderWorkbenchProps } from "./recorder-workbench";

/**
 * The page for a run that is happening now: a plain explanation first, the developer view folded
 * away. It is not keyed by trace digest, so the caption and the open/closed state survive updates.
 */
export function LiveRecorder(props: RecorderWorkbenchProps) {
  const { entry, trace } = props;
  const [technicalOpen, setTechnicalOpen] = useState(false);
  const latest = useMemo(() => projectReplay(trace, trace.events.length - 1), [trace]);
  useEffect(() => {
    document.title = LIVE_TITLE;
  }, []);
  const onToggle = (event: SyntheticEvent<HTMLDetailsElement>) =>
    setTechnicalOpen(event.currentTarget.open);
  return (
    <main className={styles.page}>
      <LiveExplainer
        mode={entry.mode}
        projection={latest}
        runFinished={trace.finished_at !== null}
      />
      <details className={styles.technical} onToggle={onToggle} open={technicalOpen}>
        <summary>Technical details for developers</summary>
        <RecorderWorkbench {...props} embedded key={`${entry.id}:${entry.trace_sha256}`} />
      </details>
    </main>
  );
}
