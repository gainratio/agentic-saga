import type { ReplayProjection } from "../replay/project-replay";
import type { ScenarioIndexEntry } from "../scenarios/schema";
import { type PlainStep, plainLiveStory } from "../story/plain-live-story";
import styles from "./live-explainer.module.css";

export interface LiveExplainerProps {
  readonly mode: ScenarioIndexEntry["mode"];
  readonly presentation?: ScenarioIndexEntry["presentation"];
  readonly runFinished: boolean;
  readonly projection: ReplayProjection;
}

export const LIVE_TITLE = "What if an AI agent's order fails halfway?";
export const ORDER_CONCERN_TITLE = "I paid $79 and never got my order — can an AI fix it safely?";

const CUSTOMER_QUOTE =
  "“I paid $79 four days ago for order_8841 and still haven’t received it — please rush a replacement or refund me.”";

const FOUR_LAYERS = [
  {
    title: "1. Customer message check",
    detail:
      "Language model accepts stuck-order requests, or politely replies when a prompt is not an order issue.",
  },
  {
    title: "2. Step chooser",
    detail: "Constrained decision engine (Jev) selects one allowed step at a time.",
  },
  {
    title: "3. State tracker",
    detail: "Durable coordinator checks lost replies, verifies proof, and runs undo newest-first.",
  },
  {
    title: "4. Paired tools",
    detail: "Strict forward and undo tools make and reverse each change safely.",
  },
] as const;

export function liveTitleFor(presentation?: ScenarioIndexEntry["presentation"]): string {
  return presentation === "order-concern" ? ORDER_CONCERN_TITLE : LIVE_TITLE;
}

/** The live demo told for anyone: what the tool is for, what is happening now, and the result. */
export function LiveExplainer({ mode, presentation, projection, runFinished }: LiveExplainerProps) {
  const isOrderConcern = presentation === "order-concern";
  const story = plainLiveStory(projection, { presentation, runFinished });
  const finished = story.summary !== null;
  const title = liveTitleFor(presentation);
  return (
    <section aria-labelledby="live-explainer-title" className={styles.explainer}>
      <p className={styles.eyebrow}>Live demo · running on your computer now</p>
      <h1 id="live-explainer-title">{title}</h1>
      {isOrderConcern ? <OrderConcernIntro /> : <EcommerceIntro />}
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
        <StepList
          label={isOrderConcern ? "Fixing the stuck order" : "The order"}
          steps={story.forward}
        />
        <StepList
          label={
            isOrderConcern
              ? "Putting things back, if the original parcel is already on the truck"
              : "Putting things back, only if a step fails"
          }
          steps={story.undo}
        />
      </div>
      <p className={styles.note}>{honestyNote(mode, isOrderConcern)}</p>
    </section>
  );
}

function EcommerceIntro() {
  return (
    <p className={styles.intro}>
      When an AI agent (software that carries out tasks for you) does a multi-step job like an
      online order, one step can fail after others have already happened. This tool then undoes the
      earlier steps, newest first, so you're never left with a charged card and no delivery.
    </p>
  );
}

function OrderConcernIntro() {
  return (
    <>
      <p className={styles.intro}>
        A customer writes in about a paid order that hasn't arrived. An AI agent (software that
        carries out tasks for you) checks that the message is really an order issue, picks one
        allowed fix step at a time, and automatically undoes every step newest-first if the depot
        finds the original parcel is already on the delivery truck.
      </p>
      <figure aria-label="Customer message" className={styles.promptBox}>
        <figcaption>Customer message</figcaption>
        <blockquote>{CUSTOMER_QUOTE}</blockquote>
      </figure>
      <ol aria-label="How the four parts work together" className={styles.layerStrip}>
        {FOUR_LAYERS.map((layer) => (
          <li key={layer.title}>
            <strong>{layer.title}</strong>
            <span>{layer.detail}</span>
          </li>
        ))}
      </ol>
    </>
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

function honestyNote(mode: ScenarioIndexEntry["mode"], isOrderConcern: boolean): string {
  const venue = isOrderConcern ? "shipping desk" : "shop";
  if (mode === "scripted") {
    return `The agent here follows a fixed script and the ${venue} is pretend, so no real money moves and no AI service is called.`;
  }
  if (mode === "live") {
    return `The steps are chosen by a real AI service. The ${venue} is pretend, so no real money moves.`;
  }
  return `The ${venue} is pretend, so no real money moves.`;
}
