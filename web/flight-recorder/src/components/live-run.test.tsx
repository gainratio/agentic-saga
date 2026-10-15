import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { parseScenarioIndex } from "../scenarios/repository";
import { loadIndexFixture } from "../test/load-index-fixture";
import { loadTraceFixture } from "../test/load-trace-fixture";
import { parseRunTrace } from "../trace/parse-run-trace";
import type { RunTrace } from "../trace/schema";
import { RecorderWorkbench } from "./recorder-workbench";

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

describe("live run view", () => {
  it("says the run is happening now while forward steps are still running", () => {
    render(<RecorderWorkbench {...liveProps(unfinished(4))} onSelectRun={vi.fn()} />);

    expect(screen.getByRole("heading", { name: "Agentic Saga · live run" })).toBeVisible();
    expect(screen.getByText(/Running now on a local Temporal server/)).toBeVisible();
    expect(screen.getByText("Status").parentElement).toHaveTextContent("Running");
    expect(screen.getByText(/no API key/)).toBeVisible();
    expect(screen.queryByText(/Replay only reads recorded evidence/)).toBeNull();
    expect(screen.queryByText(/Recording ended before completion/)).toBeNull();
  });

  it("says earlier steps are being undone while compensation runs", () => {
    render(<RecorderWorkbench {...liveProps(unfinished(11))} onSelectRun={vi.fn()} />);

    expect(screen.getByText(/A step failed\. Undoing the finished steps/)).toBeVisible();
    expect(screen.getByText("Status").parentElement).toHaveTextContent("Undoing earlier steps");
  });

  it("shows the verified outcome once the live run finishes", () => {
    render(<RecorderWorkbench {...liveProps(failureTrace())} onSelectRun={vi.fn()} />);

    expect(screen.getByRole("heading", { name: "Agentic Saga · live run" })).toBeVisible();
    expect(screen.getByText("Status").parentElement).toHaveTextContent("Safely undone");
    expect(
      screen.getByText(
        "Order failed. Recovery succeeded: all completed changes were safely undone.",
      ),
    ).toBeVisible();
  });
});
