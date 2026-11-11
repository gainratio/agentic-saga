import { fireEvent, render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { describe, expect, it, vi } from "vitest";
import { parseScenarioIndex } from "../scenarios/repository";
import { loadIndexFixture } from "../test/load-index-fixture";
import { loadTraceFixture } from "../test/load-trace-fixture";
import { parseRunTrace } from "../trace/parse-run-trace";
import type { RunTrace } from "../trace/schema";
import { LiveRecorder } from "./live-recorder";

const JARGON = /saga|compensat|workflow|temporal|activit|idempot|invariant|reconcil|provider/i;

function liveProps(trace: RunTrace) {
  const index = parseScenarioIndex(loadIndexFixture());
  if (!index.ok) throw new Error("real fixture must satisfy browser contracts");
  const entry = index.value.runs.find(({ id }) => id === "business-failure");
  if (!entry) throw new Error("real fixture must contain the failure run");
  return { entry, index: { ...index.value, runs: [entry], live: true }, trace };
}

function failureTrace(): RunTrace {
  const parsed = parseRunTrace(loadTraceFixture("business-failure"));
  if (!parsed.ok) throw new Error("fixture must be valid");
  return parsed.trace;
}

function unfinished(eventCount: number): RunTrace {
  const full = failureTrace();
  const events = full.events.slice(0, eventCount);
  const last = events.at(-1);
  if (!last) throw new Error("need at least one event");
  return { ...full, events, finished_at: null, outcome: last.after_status, proofs: [] };
}

function explainer(): HTMLElement {
  return screen.getByRole("region", { name: "What if an AI agent's order fails halfway?" });
}

describe("live run view", () => {
  it("explains the tool in plain words before anything else", () => {
    render(<LiveRecorder {...liveProps(unfinished(4))} onSelectRun={vi.fn()} />);

    expect(
      screen.getByRole("heading", { level: 1, name: "What if an AI agent's order fails halfway?" }),
    ).toBeVisible();
    expect(
      screen.getByText(/so you're never left with a charged card and no delivery/),
    ).toBeVisible();
    expect(
      screen.getByText(/an AI agent \(software that carries out tasks for you\)/),
    ).toBeVisible();
    expect(screen.getByText(/no real money moves and no AI service is called/)).toBeVisible();
  });

  it("names the browser tab in plain words", () => {
    render(<LiveRecorder {...liveProps(unfinished(4))} onSelectRun={vi.fn()} />);

    expect(document.title).toBe("What if an AI agent's order fails halfway?");
  });

  it("captions what is happening right now and shows each step's status", () => {
    render(<LiveRecorder {...liveProps(unfinished(4))} onSelectRun={vi.fn()} />);

    expect(screen.getByRole("status", { name: "Right now" })).toHaveTextContent(
      "Charging the card…",
    );
    expect(screen.getByRole("listitem", { name: "Reserve the item: Done" })).toBeVisible();
    expect(screen.getByRole("listitem", { name: "Charge the card: Working…" })).toBeVisible();
  });

  it("explains why the earlier steps are being undone", () => {
    render(<LiveRecorder {...liveProps(unfinished(12))} onSelectRun={vi.fn()} />);

    expect(screen.getByRole("status", { name: "Right now" })).toHaveTextContent(
      "Refunded the card, because the order can't go through.",
    );
    expect(screen.getByRole("listitem", { name: "Charge the card: Reversed" })).toBeVisible();
  });

  it("ends with a plain summary of the outcome", () => {
    render(<LiveRecorder {...liveProps(failureTrace())} onSelectRun={vi.fn()} />);

    expect(screen.getByRole("status", { name: "Result" })).toHaveTextContent(
      "Nothing was left half-done.",
    );
  });

  it("keeps technical words out of the explanation", () => {
    for (const count of [1, 4, 9, 10, 12, 15]) {
      const view = render(<LiveRecorder {...liveProps(unfinished(count))} onSelectRun={vi.fn()} />);
      expect(explainer().textContent).not.toMatch(JARGON);
      view.unmount();
    }
  });

  it("hides the developer view until asked, then shows the live run header", () => {
    render(<LiveRecorder {...liveProps(unfinished(4))} onSelectRun={vi.fn()} />);
    const technical = screen.getByText("Technical details for developers");

    expect(screen.getByRole("heading", { name: "Agentic Saga · live run" })).not.toBeVisible();
    fireEvent.click(technical);

    expect(screen.getByRole("heading", { name: "Agentic Saga · live run" })).toBeVisible();
    expect(
      within(screen.getByText("Status").parentElement as HTMLElement).getByText("Running"),
    ).toBeVisible();
  });

  it("has no detectable accessibility violations", async () => {
    const { container } = render(
      <LiveRecorder {...liveProps(unfinished(9))} onSelectRun={vi.fn()} />,
    );

    const result = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });

    expect(result.violations).toEqual([]);
  });

  it("renders the 4-layer customer order-concern story without technical jargon", () => {
    const toolMap: Readonly<Record<string, string>> = {
      reserve_inventory: "reserve_replacement",
      charge_payment: "issue_shipping_credit",
      schedule_fulfillment: "book_express_courier",
      verify_order: "verify_original_stopped",
      cancel_fulfillment: "cancel_express_courier",
      refund_payment: "reverse_shipping_credit",
      release_inventory: "release_replacement",
    };
    const base = failureTrace();
    const mapped: RunTrace = {
      ...base,
      events: base.events.map((event) => ({
        ...event,
        tool_name: event.tool_name ? (toolMap[event.tool_name] ?? event.tool_name) : null,
      })),
    };
    const props = liveProps(mapped);
    const entry = {
      ...props.entry,
      id: "already-in-transit",
      name: "Original parcel scanned onto truck",
      presentation: "order-concern" as const,
    };

    for (const count of [1, 4, 9, 10, 12, 15]) {
      const partial =
        count === mapped.events.length
          ? mapped
          : {
              ...mapped,
              events: mapped.events.slice(0, count),
              finished_at: null,
              outcome: mapped.events[count - 1]?.after_status ?? mapped.outcome,
              proofs: [],
            };
      const view = render(
        <LiveRecorder
          entry={entry}
          index={{ ...props.index, runs: [entry] }}
          onSelectRun={vi.fn()}
          trace={partial}
        />,
      );
      const section = screen.getByRole("region", {
        name: "I paid $79 and never got my order — can an AI fix it safely?",
      });
      expect(section.textContent).not.toMatch(JARGON);
      if (count === mapped.events.length) {
        expect(screen.getByLabelText("Customer message")).toHaveTextContent("order_8841");
        expect(screen.getByLabelText("How the four parts work together")).toBeVisible();
        expect(screen.getByRole("status", { name: "Result" })).toHaveTextContent(
          "No duplicate shipment went out.",
        );
        fireEvent.click(screen.getByText("Technical details for developers"));
        expect(
          screen.getByRole("heading", {
            name: "One customer concern. Four layers. One safe outcome.",
          }),
        ).toBeVisible();
      }
      view.unmount();
    }
  });
});
