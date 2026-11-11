import type { ReplayProjection } from "../replay/project-replay";
import type { TraceEvent } from "../trace/schema";

export type FlowPhase = "compensated" | "compensating" | "human" | "running" | "succeeded";
export type FlowStepState =
  | "active"
  | "attention"
  | "checking"
  | "complete"
  | "failed"
  | "reversed"
  | "waiting";

export interface FlowStep {
  readonly detail: string;
  readonly id: string;
  readonly label: string;
  readonly state: FlowStepState;
}

export interface EcommerceFlow {
  readonly detail: string;
  readonly forward: readonly FlowStep[];
  readonly headline: string;
  readonly phase: FlowPhase;
  readonly rollback: readonly FlowStep[];
}

interface StepDefinition {
  readonly detail: string;
  readonly id: string;
  readonly label: string;
  readonly tool: string;
}

interface DomainFlowConfig {
  readonly forward: readonly StepDefinition[];
  readonly rollback: readonly StepDefinition[];
  readonly reversalTools: Readonly<Record<string, string>>;
  readonly proofTool: string;
  readonly blockedForwardTool: string;
  readonly completedHeadline: string;
  readonly reconciledHeadline: string;
  readonly unknownHeadline: string;
  readonly startHeadline: string;
}

const ECOMMERCE_CONFIG: DomainFlowConfig = {
  forward: [
    {
      id: "reserve-inventory",
      label: "Reserve item",
      detail: "Reserve the requested stock",
      tool: "reserve_inventory",
    },
    {
      id: "charge-payment",
      label: "Charge payment",
      detail: "Capture the order total",
      tool: "charge_payment",
    },
    {
      id: "schedule-fulfillment",
      label: "Arrange delivery",
      detail: "Ask fulfillment to ship",
      tool: "schedule_fulfillment",
    },
    {
      id: "verify-order",
      label: "Verify order",
      detail: "Read the final provider state",
      tool: "verify_order",
    },
  ],
  rollback: [
    {
      id: "cancel-fulfillment",
      label: "Cancel delivery",
      detail: "Undo fulfillment first",
      tool: "cancel_fulfillment",
    },
    {
      id: "refund-payment",
      label: "Refund payment",
      detail: "Return the captured total",
      tool: "refund_payment",
    },
    {
      id: "release-inventory",
      label: "Release stock",
      detail: "Return the held item",
      tool: "release_inventory",
    },
  ],
  reversalTools: {
    "charge-payment": "refund_payment",
    "reserve-inventory": "release_inventory",
    "schedule-fulfillment": "cancel_fulfillment",
  },
  proofTool: "verify_order",
  blockedForwardTool: "schedule_fulfillment",
  completedHeadline: "Order completed safely",
  reconciledHeadline: "Provider evidence confirms the payment completed",
  unknownHeadline: "Checking what the payment provider actually did",
  startHeadline: "The agent is reading the order goal",
};

const ORDER_CONCERN_CONFIG: DomainFlowConfig = {
  forward: [
    {
      id: "reserve-replacement",
      label: "Reserve replacement",
      detail: "Hold replacement stock for order_8841",
      tool: "reserve_replacement",
    },
    {
      id: "issue-shipping-credit",
      label: "Issue $15 credit",
      detail: "Apply courtesy shipping credit",
      tool: "issue_shipping_credit",
    },
    {
      id: "book-express-courier",
      label: "Book express courier",
      detail: "Schedule priority dispatch",
      tool: "book_express_courier",
    },
    {
      id: "verify-original-stopped",
      label: "Verify original stopped",
      detail: "Confirm original parcel is still at depot",
      tool: "verify_original_stopped",
    },
  ],
  rollback: [
    {
      id: "cancel-express-courier",
      label: "Cancel express courier",
      detail: "Undo courier booking first",
      tool: "cancel_express_courier",
    },
    {
      id: "reverse-shipping-credit",
      label: "Reverse $15 credit",
      detail: "Remove courtesy shipping credit",
      tool: "reverse_shipping_credit",
    },
    {
      id: "release-replacement",
      label: "Release replacement",
      detail: "Return held replacement stock",
      tool: "release_replacement",
    },
  ],
  reversalTools: {
    "book-express-courier": "cancel_express_courier",
    "issue-shipping-credit": "reverse_shipping_credit",
    "reserve-replacement": "release_replacement",
  },
  proofTool: "verify_original_stopped",
  blockedForwardTool: "book_express_courier",
  completedHeadline: "Express replacement verified and ready",
  reconciledHeadline: "Provider evidence confirms one courtesy credit",
  unknownHeadline: "Checking whether the courtesy credit already applied",
  startHeadline: "Triage accepted the customer's stuck-order concern",
};

export function projectEcommerceFlow(projection: ReplayProjection): EcommerceFlow {
  return projectDomainFlow(projection, ECOMMERCE_CONFIG);
}

export function projectOrderConcernFlow(projection: ReplayProjection): EcommerceFlow {
  return projectDomainFlow(projection, ORDER_CONCERN_CONFIG);
}

function projectDomainFlow(projection: ReplayProjection, config: DomainFlowConfig): EcommerceFlow {
  const phase = phaseFor(projection);
  return {
    detail: detailFor(phase),
    forward: config.forward.map((definition) => forwardStep(definition, projection.events, config)),
    headline: headlineFor(projection, config),
    phase,
    rollback: config.rollback.map((definition) => stepFor(definition, projection.events)),
  };
}

function forwardStep(
  definition: StepDefinition,
  events: readonly TraceEvent[],
  config: DomainFlowConfig,
): FlowStep {
  const reversal = config.reversalTools[definition.id];
  if (reversal && toolState(events, reversal) === "complete") return step(definition, "reversed");
  if (definition.tool === config.proofTool) {
    return step(definition, verificationState(events, config.proofTool));
  }
  if (definition.tool === config.blockedForwardTool && proofRejected(events, config.proofTool)) {
    return step(definition, "failed");
  }
  return stepFor(definition, events);
}

function stepFor(definition: StepDefinition, events: readonly TraceEvent[]): FlowStep {
  return step(definition, toolState(events, definition.tool));
}

function step(definition: StepDefinition, state: FlowStepState): FlowStep {
  return { detail: definition.detail, id: definition.id, label: definition.label, state };
}

function toolState(events: readonly TraceEvent[], tool: string): FlowStepState {
  const matching = events.filter((event) => event.tool_name === tool);
  if (matching.length === 0) return "waiting";
  if (matching.some(confirmsEffect)) return "complete";
  if (matching.some(needsAttention)) {
    const needsHuman = events.at(-1)?.after_status === "human_required";
    return needsHuman && matching.some((event) => event.direction === "compensation")
      ? "attention"
      : "checking";
  }
  return matching.some((event) => event.event_type.endsWith("observed")) ? "complete" : "active";
}

function confirmsEffect(event: TraceEvent): boolean {
  return (
    outputKind(event) === "effect_confirmed" || outputKind(event) === "reconcile_effect_confirmed"
  );
}

function needsAttention(event: TraceEvent): boolean {
  const kind = outputKind(event);
  return (
    kind === "outcome_unknown" || kind === "reconcile_conflict" || kind === "reconcile_pending"
  );
}

function outputKind(event: TraceEvent): unknown {
  return event.redacted_output?.kind;
}

function verificationState(events: readonly TraceEvent[], proofTool: string): FlowStepState {
  const observed = events.findLast(
    (event) => event.tool_name === proofTool && event.event_type === "invariant_evaluated",
  );
  if (observed) return observed.redacted_output?.verified === true ? "complete" : "failed";
  return events.some((event) => event.tool_name === proofTool) ? "active" : "waiting";
}

function proofRejected(events: readonly TraceEvent[], proofTool: string): boolean {
  return events.some(
    (event) => event.tool_name === proofTool && event.redacted_output?.verified === false,
  );
}

function phaseFor(projection: ReplayProjection): FlowPhase {
  if (projection.currentStatus === "human_required") return "human";
  if (projection.currentStatus === "compensated_verified") return "compensated";
  if (projection.currentStatus === "succeeded_verified") return "succeeded";
  if (projection.currentStatus === "compensating") return "compensating";
  return "running";
}

function headlineFor(projection: ReplayProjection, config: DomainFlowConfig): string {
  if (projection.currentStatus === "human_required") return "Paused safely for human review";
  if (projection.currentStatus === "compensated_verified") {
    return "Every completed change was safely undone";
  }
  if (projection.currentStatus === "succeeded_verified") return config.completedHeadline;
  if (outputKind(projection.currentEvent) === "outcome_unknown") {
    return config.unknownHeadline;
  }
  if (projection.currentEvent.event_type === "reconciliation_recorded") {
    return config.reconciledHeadline;
  }
  if (projection.currentEvent.event_type === "compensation_started") {
    return "A later step failed—starting safe undo actions";
  }
  const label = labelForTool(projection.currentEvent.tool_name, config);
  if (label) return `${actionVerb(projection.currentEvent)} ${label.toLowerCase()}`;
  return config.startHeadline;
}

function actionVerb(event: TraceEvent): string {
  if (event.event_type.endsWith("observed") || event.event_type === "effect_outcome_recorded") {
    return "Completed";
  }
  return event.direction === "compensation" ? "Undoing" : "Working on";
}

function labelForTool(tool: string | null, config: DomainFlowConfig): string | undefined {
  return [...config.forward, ...config.rollback].find((item) => item.tool === tool)?.label;
}

function detailFor(phase: FlowPhase): string {
  if (phase === "human")
    return "The recovery result is still uncertain, so the system stops before making another change.";
  if (phase === "compensated")
    return "The order could not finish, but inventory, payment, and fulfillment are verified safe.";
  if (phase === "compensating")
    return "The safety layer reveals one valid undo at a time, in reverse order.";
  if (phase === "succeeded")
    return "Fresh evidence confirms the inventory, payment, and delivery state.";
  return "The agent chooses what to do next; deterministic safety code approves and records every change.";
}
