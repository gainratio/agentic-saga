import type { ReplayProjection } from "../replay/project-replay";
import type { ScenarioIndexEntry } from "../scenarios/schema";
import { type PlainStep, plainLiveStory } from "../story/plain-live-story";
import styles from "./live-explainer.module.css";

export interface LiveExplainerProps {
  readonly mode: ScenarioIndexEntry["mode"];
  readonly projection: ReplayProjection;
}

const TITLE = "What if an AI agent's order fails halfway?";

/** The live demo told for anyone: what the tool is for, what is happening now, and the result. */
export function LiveExplainer({ mode, projection }: LiveExplainerProps) {
  const story = plainLiveStory(projection);
  const finished = story.summary !== null;
  return (
    <section aria-labelledby="live-explainer-title" className={styles.explainer}>
      <p className={styles.eyebrow}>Live demo · running on your computer now</p>
      <h1 id="live-explainer-title">{TITLE}</h1>
      <p className={styles.intro}>
        When an AI agent does a multi-step job like an online order, one step can fail after others
        have already happened. This tool then undoes the earlier steps, newest first, so you're
        never left with a charged card and no delivery.
      </p>
      <div
        aria-label={finished ? "Result" : "Right now"}
        className={styles.caption}
        data-finished={finished}
        role="status"
      >
        <strong>{finished ? "Result" : "Right now"}</strong>
        <span>{story.summary ?? story.caption}</span>
      </div>
      <div className={styles.lanes}>
        <StepList label="The order" steps={story.forward} />
        <StepList label="Putting things back, only if a step fails" steps={story.undo} />
      </div>
      <p className={styles.note}>{honestyNote(mode)}</p>
    </section>
  );
}

interface StepListProps {
  readonly label: string;
  readonly steps: readonly PlainStep[];
}

function StepList({ label, steps }: StepListProps) {
  return (
    <section aria-label={label} className={styles.lane}>
      <h2>{label}</h2>
      <ol>
        {steps.map((step) => (
          <li aria-label={`${step.label}: ${step.status}`} data-state={step.state} key={step.id}>
            <span>{step.label}</span>
            <small>{step.status}</small>
          </li>
        ))}
      </ol>
    </section>
  );
}

function honestyNote(mode: ScenarioIndexEntry["mode"]): string {
  if (mode === "scripted") {
    return "The agent here follows a fixed script and the shop is pretend, so no real money moves and no AI service is called.";
  }
  if (mode === "live") {
    return "The steps are chosen by a real AI service. The shop is pretend, so no real money moves.";
  }
  return "The shop is pretend, so no real money moves.";
}
