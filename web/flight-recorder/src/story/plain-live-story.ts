import type { ReplayProjection } from "../replay/project-replay";
import type { ScenarioIndexEntry } from "../scenarios/schema";
import type { TraceEvent } from "../trace/schema";
import {
  type EcommerceFlow,
  type FlowStep,
  type FlowStepState,
  projectEcommerceFlow,
  projectOrderConcernFlow,
} from "./project-ecommerce-flow";

/** The live run told in everyday words, for someone who has never seen the code. */
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

interface StoryCopy {
  readonly project: (projection: ReplayProjection) => EcommerceFlow;
  readonly proofId: string;
  readonly chargeTool: string;
  readonly labels: Readonly<Record<string, string>>;
  readonly doing: Readonly<Record<string, string>>;
  readonly done: Readonly<Record<string, string>>;
  readonly undone: Readonly<Record<string, string>>;
  readonly undoComplete: Readonly<Record<string, string>>;
  readonly summary: Readonly<Record<string, string>>;
  readonly fixedCaptions: Readonly<Record<string, string>>;
  readonly settledReply: string;
  readonly lostChargeReply: string;
  readonly proofPassed: string;
  readonly proofFailed: string;
  readonly unknownUndoCaption: string;
  readonly checkingUndoReply: string;
}

const STATUS: Readonly<Record<FlowStepState, string>> = {
  active: "Working…",
  attention: "Needs a person",
  checking: "Checking…",
  complete: "Done",
  failed: "Failed",
  reversed: "Reversed",
  waiting: "Waiting",
};

const ECOMMERCE_COPY: StoryCopy = {
  project: projectEcommerceFlow,
  proofId: "verify-order",
  chargeTool: "charge_payment",
  labels: {
    "reserve-inventory": "Reserve the item",
    "charge-payment": "Charge the card",
    "schedule-fulfillment": "Book the delivery",
    "verify-order": "Check the order went through",
    "cancel-fulfillment": "Cancel the delivery",
    "refund-payment": "Refund the card",
    "release-inventory": "Release the item",
  },
  doing: {
    reserve_inventory: "Reserving the item…",
    charge_payment: "Charging the card…",
    schedule_fulfillment: "Booking the delivery…",
    verify_order: "Checking that the order went through…",
  },
  done: {
    reserve_inventory: "The item is reserved.",
    charge_payment: "The card was charged.",
    schedule_fulfillment: "The delivery is booked.",
  },
  undone: {
    cancel_fulfillment: "Cancelled the delivery, because the order can't go through.",
    refund_payment: "Refunded the card, because the order can't go through.",
    release_inventory: "Released the item back to the shop.",
  },
  undoComplete: {
    "cancel-fulfillment": "Cancelled",
    "refund-payment": "Refunded",
    "release-inventory": "Released",
  },
  summary: {
    aborted_clean: "Stopped before changing anything. Nothing needs to be put back.",
    compensated_verified:
      "The order couldn't be completed, so everything was put back: the delivery was cancelled, the card was refunded, and the item was released. Nothing was left half-done.",
    human_required:
      "Stopped safely: the refund couldn't be confirmed, so a person has to check it before anything else happens.",
    succeeded_verified:
      "The order went through: the item is reserved, the card was charged once, and the delivery is booked. A final check confirmed it.",
  },
  fixedCaptions: {
    compensation_completed: "Every earlier step is undone.",
    compensation_started:
      "The delivery company turned the order down, so the earlier steps are being undone, newest first.",
    human_required: "Paused: a person must confirm the refund before anything else happens.",
    human_resolved:
      "The person confirmed the refund (in this demo, the script plays that person). Finishing the undo.",
    saga_started: "Starting the order.",
    terminal_assigned: "Finished.",
  },
  settledReply: "The card company confirmed one charge.",
  lostChargeReply:
    "The card company didn't reply. Asking whether the card was charged, instead of charging it again.",
  proofPassed: "The final check passed: the order really went through.",
  proofFailed: "The final check failed: the delivery company turned the order down.",
  unknownUndoCaption:
    "The refund can't be confirmed. Stopping to ask a person instead of guessing.",
  checkingUndoReply: "Asking the card company whether the refund went through…",
};

const ORDER_CONCERN_COPY: StoryCopy = {
  project: projectOrderConcernFlow,
  proofId: "verify-original-stopped",
  chargeTool: "issue_shipping_credit",
  labels: {
    "reserve-replacement": "Hold a replacement item",
    "issue-shipping-credit": "Add a $15 courtesy credit",
    "book-express-courier": "Book an express courier",
    "verify-original-stopped": "Check the original parcel stopped",
    "cancel-express-courier": "Cancel the express courier",
    "reverse-shipping-credit": "Remove the $15 courtesy credit",
    "release-replacement": "Put the replacement item back",
  },
  doing: {
    reserve_replacement: "Holding a replacement item for order_8841…",
    issue_shipping_credit: "Adding a $15 courtesy shipping credit…",
    book_express_courier: "Booking an express courier…",
    verify_original_stopped: "Checking whether the original parcel is still at the depot…",
  },
  done: {
    reserve_replacement: "A replacement item is held.",
    issue_shipping_credit: "The $15 courtesy credit was applied.",
    book_express_courier: "The express courier is booked.",
  },
  undone: {
    cancel_express_courier:
      "Cancelled the express courier, because the original parcel is already on the truck.",
    reverse_shipping_credit:
      "Removed the $15 courtesy credit, because the original parcel is on its way.",
    release_replacement: "Put the replacement item back into stock.",
  },
  undoComplete: {
    "cancel-express-courier": "Cancelled",
    "reverse-shipping-credit": "Removed",
    "release-replacement": "Released",
  },
  summary: {
    aborted_clean: "Stopped before changing anything. Nothing needs to be put back.",
    compensated_verified:
      "The original parcel was just scanned onto a delivery truck, so every new step was undone newest-first: the express courier was cancelled, the $15 credit was removed, and the replacement item was put back. No duplicate shipment went out.",
    human_required:
      "Stopped safely: removing the $15 credit couldn't be confirmed, so a support lead must check it before anything else happens.",
    succeeded_verified:
      "The depot confirmed the original parcel stopped, so the replacement item is held, the $15 credit was applied once, and the express courier is booked.",
  },
  fixedCaptions: {
    compensation_completed: "Every earlier step is undone.",
    compensation_started:
      "The original parcel was just scanned onto a delivery truck, so the earlier steps are being undone, newest first.",
    human_required:
      "Paused: a support lead must confirm the $15 credit removal before anything else happens.",
    human_resolved:
      "The support lead confirmed the credit removal (in this demo, the script plays that person). Finishing the undo.",
    saga_started: "Customer concern accepted. Starting the stuck-order fix.",
    terminal_assigned: "Finished.",
  },
  settledReply: "Billing confirmed one $15 courtesy credit.",
  lostChargeReply:
    "Billing didn't reply. Asking whether the $15 credit already applied, instead of crediting twice.",
  proofPassed: "The depot check passed: the original parcel was stopped before leaving.",
  proofFailed: "The depot check failed: the original parcel was just scanned onto a truck.",
  unknownUndoCaption:
    "The $15 credit removal can't be confirmed. Stopping to ask a support lead instead of guessing.",
  checkingUndoReply: "Asking billing whether the $15 credit removal went through…",
};

export interface PlainStoryOptions {
  readonly presentation?: ScenarioIndexEntry["presentation"];
  /** False while the run is still going: a pause for a person is then not the end. */
  readonly runFinished?: boolean;
}

/** Statuses that end a run. A pause for a person only ends a run that has stopped. */
const FINAL_STATUSES = new Set(["aborted_clean", "compensated_verified", "succeeded_verified"]);

export function plainLiveStory(
  projection: ReplayProjection,
  options: PlainStoryOptions = {},
): PlainStory {
  const copy = options.presentation === "order-concern" ? ORDER_CONCERN_COPY : ECOMMERCE_COPY;
  const flow = copy.project(projection);
  const status = projection.currentStatus;
  const ended = FINAL_STATUSES.has(status) || options.runFinished !== false;
  return {
    caption: captionWithContext(projection.events, projection.currentEvent, copy),
    forward: flow.forward.map((item) => plainStep(item, copy)),
    summary: ended ? (copy.summary[status] ?? null) : null,
    undo: markNextUndo(
      flow.rollback.map((item) => plainStep(item, copy)),
      projection.currentStatus,
    ),
  };
}

/** While undoing, show which undo comes next so nothing looks stuck on "Waiting". */
function markNextUndo(steps: readonly PlainStep[], status: string): readonly PlainStep[] {
  if (status !== "compensating" || steps.some((step) => step.state === "active")) return steps;
  const next = steps.findIndex((step) => step.state === "waiting");
  return steps.map((step, index) => (index === next ? { ...step, status: "Up next" } : step));
}

function plainStep(step: FlowStep, copy: StoryCopy): PlainStep {
  const state = settledState(step, copy.proofId);
  const undone = state === "complete" ? copy.undoComplete[step.id] : undefined;
  return {
    id: step.id,
    label: copy.labels[step.id] ?? step.label,
    state,
    status: undone ?? STATUS[state],
  };
}

/** Only the final check fails; the steps before it did happen until they are undone. */
function settledState(step: FlowStep, proofId: string): FlowStepState {
  return step.state === "failed" && step.id !== proofId ? "complete" : step.state;
}

/** When the agent moves on at once, keep the news that let it move on. */
function captionWithContext(
  events: readonly TraceEvent[],
  current: TraceEvent,
  copy: StoryCopy,
): string {
  const previous = events.at(-2);
  const settled =
    current.event_type === "agent_decision_recorded" &&
    previous?.event_type === "reconciliation_recorded";
  const next = captionFor(current, copy);
  return settled ? `${copy.settledReply} ${next}` : next;
}

function captionFor(event: TraceEvent, copy: StoryCopy): string {
  if (event.event_type === "agent_decision_recorded") {
    return event.tool_name === null
      ? "Every step is done. Wrapping up."
      : (copy.doing[event.tool_name] ?? "Working…");
  }
  if (event.event_type === "compensation_outcome_recorded") {
    return outputKind(event) === "outcome_unknown"
      ? copy.unknownUndoCaption
      : (copy.undone[event.tool_name ?? ""] ?? "Working…");
  }
  if (event.event_type === "effect_outcome_recorded") {
    return outputKind(event) === "outcome_unknown"
      ? lostReply(event, copy)
      : (copy.done[event.tool_name ?? ""] ?? "Working…");
  }
  if (event.event_type === "invariant_evaluated") {
    return event.redacted_output?.verified === true ? copy.proofPassed : copy.proofFailed;
  }
  if (event.event_type === "reconciliation_recorded") {
    return outputKind(event) === "reconcile_effect_confirmed"
      ? `${copy.settledReply} Carrying on.`
      : copy.checkingUndoReply;
  }
  return copy.fixedCaptions[event.event_type] ?? "Working…";
}

function lostReply(event: TraceEvent, copy: StoryCopy): string {
  if (event.tool_name === copy.chargeTool) return copy.lostChargeReply;
  return "No reply yet. Asking what happened instead of trying again.";
}

function outputKind(event: TraceEvent): unknown {
  return event.redacted_output?.kind;
}
