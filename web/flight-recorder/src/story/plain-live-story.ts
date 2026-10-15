import type { ReplayProjection } from "../replay/project-replay";
import type { TraceEvent } from "../trace/schema";
import { type FlowStep, type FlowStepState, projectEcommerceFlow } from "./project-ecommerce-flow";

/** The checkout told in everyday words, for someone who has never seen the code. */
export interface PlainStory {
  readonly caption: string;
  readonly forward: readonly PlainStep[];
  readonly summary: string | null;
  readonly undo: readonly PlainStep[];
}

export interface PlainStep {
  readonly id: string;
  readonly label: string;
  readonly state: FlowStepState;
  readonly status: string;
}

const LABELS: Readonly<Record<string, string>> = {
  "reserve-inventory": "Reserve the item",
  "charge-payment": "Charge the card",
  "schedule-fulfillment": "Book the delivery",
  "verify-order": "Check the order went through",
  "cancel-fulfillment": "Cancel the delivery",
  "refund-payment": "Refund the card",
  "release-inventory": "Release the item",
};

const STATUS: Readonly<Record<FlowStepState, string>> = {
  active: "Working…",
  attention: "Needs a person",
  checking: "Checking…",
  complete: "Done",
  failed: "Failed",
  reversed: "Reversed",
  waiting: "Waiting",
};

const DOING: Readonly<Record<string, string>> = {
  reserve_inventory: "Reserving the item…",
  charge_payment: "Charging the card…",
  schedule_fulfillment: "Booking the delivery…",
  verify_order: "Checking that the order went through…",
};

const DONE: Readonly<Record<string, string>> = {
  reserve_inventory: "The item is reserved.",
  charge_payment: "The card was charged.",
  schedule_fulfillment: "The delivery is booked.",
};

const UNDONE: Readonly<Record<string, string>> = {
  cancel_fulfillment: "Cancelled the delivery, because the order can't go through.",
  refund_payment: "Refunded the card, because the order can't go through.",
  release_inventory: "Released the item back to the shop.",
};

const SUMMARY: Readonly<Record<string, string>> = {
  aborted_clean: "Stopped before changing anything. Nothing needs to be put back.",
  compensated_verified:
    "The order couldn't be completed, so everything was put back: the delivery was cancelled, the card was refunded, and the item was released. Nothing was left half-done.",
  human_required:
    "Stopped safely: the refund couldn't be confirmed, so a person has to check it before anything else happens.",
  succeeded_verified:
    "The order went through: the item is reserved, the card was charged once, and the delivery is booked. A final check confirmed it.",
};

const FIXED_CAPTIONS: Readonly<Record<string, string>> = {
  compensation_completed: "Every earlier step is undone.",
  compensation_started:
    "The delivery company turned the order down, so the earlier steps are being undone, newest first.",
  human_required: "Paused: a person must confirm the refund before anything else happens.",
  human_resolved:
    "The person confirmed the refund (in this demo, the script plays that person). Finishing the undo.",
  saga_started: "Starting the order.",
  terminal_assigned: "Finished.",
};

type CaptionRule = (event: TraceEvent) => string | undefined;

const CAPTION_RULES: Readonly<Record<string, CaptionRule>> = {
  agent_decision_recorded: (event) =>
    event.tool_name === null ? "Every step is done. Wrapping up." : DOING[event.tool_name],
  compensation_outcome_recorded: (event) =>
    outputKind(event) === "outcome_unknown"
      ? "The refund can't be confirmed. Stopping to ask a person instead of guessing."
      : UNDONE[event.tool_name ?? ""],
  effect_outcome_recorded: (event) =>
    outputKind(event) === "outcome_unknown" ? lostReply(event) : DONE[event.tool_name ?? ""],
  invariant_evaluated: (event) =>
    event.redacted_output?.verified === true
      ? "The final check passed: the order really went through."
      : "The final check failed: the delivery company turned the order down.",
  reconciliation_recorded: (event) =>
    outputKind(event) === "reconcile_effect_confirmed"
      ? `${SETTLED_REPLY} Carrying on.`
      : "Asking the card company whether the refund went through…",
};

export interface PlainStoryOptions {
  /** False while the run is still going: a pause for a person is then not the end. */
  readonly runFinished?: boolean;
}

/** Statuses that end a run. A pause for a person only ends a run that has stopped. */
const FINAL_STATUSES = new Set(["aborted_clean", "compensated_verified", "succeeded_verified"]);

export function plainLiveStory(
  projection: ReplayProjection,
  options: PlainStoryOptions = {},
): PlainStory {
  const flow = projectEcommerceFlow(projection);
  const status = projection.currentStatus;
  const ended = FINAL_STATUSES.has(status) || options.runFinished !== false;
  return {
    caption: captionWithContext(projection.events, projection.currentEvent),
    forward: flow.forward.map(plainStep),
    summary: ended ? (SUMMARY[status] ?? null) : null,
    undo: markNextUndo(flow.rollback.map(plainStep), projection.currentStatus),
  };
}

/** While undoing, show which undo comes next so nothing looks stuck on "Waiting". */
function markNextUndo(steps: readonly PlainStep[], status: string): readonly PlainStep[] {
  if (status !== "compensating" || steps.some((step) => step.state === "active")) return steps;
  const next = steps.findIndex((step) => step.state === "waiting");
  return steps.map((step, index) => (index === next ? { ...step, status: "Up next" } : step));
}

/** A finished undo says what it did, so "Done" never sits next to "Undone". */
const UNDO_COMPLETE: Readonly<Record<string, string>> = {
  "cancel-fulfillment": "Cancelled",
  "refund-payment": "Refunded",
  "release-inventory": "Released",
};

function plainStep(step: FlowStep): PlainStep {
  const state = settledState(step);
  const undone = state === "complete" ? UNDO_COMPLETE[step.id] : undefined;
  return {
    id: step.id,
    label: LABELS[step.id] ?? step.label,
    state,
    status: undone ?? STATUS[state],
  };
}

/** Only the final check fails; the steps before it did happen until they are undone. */
function settledState(step: FlowStep): FlowStepState {
  return step.state === "failed" && step.id !== "verify-order" ? "complete" : step.state;
}

const SETTLED_REPLY = "The card company confirmed one charge.";

/** When the agent moves on at once, keep the news that let it move on. */
function captionWithContext(events: readonly TraceEvent[], current: TraceEvent): string {
  const previous = events.at(-2);
  const settled =
    current.event_type === "agent_decision_recorded" &&
    previous?.event_type === "reconciliation_recorded";
  return settled ? `${SETTLED_REPLY} ${captionFor(current)}` : captionFor(current);
}

function captionFor(event: TraceEvent): string {
  const rule = CAPTION_RULES[event.event_type];
  return rule?.(event) ?? FIXED_CAPTIONS[event.event_type] ?? "Working…";
}

function lostReply(event: TraceEvent): string {
  if (event.tool_name === "charge_payment") {
    return "The card company didn't reply. Asking whether the card was charged, instead of charging it again.";
  }
  return "No reply yet. Asking what happened instead of trying again.";
}

function outputKind(event: TraceEvent): unknown {
  return event.redacted_output?.kind;
}
