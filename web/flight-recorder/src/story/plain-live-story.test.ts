import { describe, expect, it } from "vitest";
import { projectReplay } from "../replay/project-replay";
import { loadTraceFixture, type TraceFixtureName } from "../test/load-trace-fixture";
import { parseRunTrace } from "../trace/parse-run-trace";
import type { RunTrace } from "../trace/schema";
import { type PlainStory, plainLiveStory } from "./plain-live-story";

const fixtures: readonly TraceFixtureName[] = [
  "business-failure",
  "compensation-failure",
  "happy-path",
  "lost-response",
];
const JARGON =
  /saga|compensat|workflow|temporal|activit|idempot|invariant|reconcil|provider|fulfil|inventory|terminal|trace/i;

function trace(name: TraceFixtureName): RunTrace {
  const parsed = parseRunTrace(loadTraceFixture(name));
  if (!parsed.ok) throw new Error("fixture must be valid");
  return parsed.trace;
}

function storyAt(name: TraceFixtureName, seq: number): PlainStory {
  return plainLiveStory(projectReplay(trace(name), seq - 1));
}

function statusOf(story: PlainStory, label: string): string | undefined {
  return [...story.forward, ...story.undo].find((step) => step.label === label)?.status;
}

describe("plain live story", () => {
  it("says what the agent is doing right now in everyday words", () => {
    const story = storyAt("business-failure", 4);

    expect(story.caption).toBe("Charging the card…");
    expect(statusOf(story, "Reserve the item")).toBe("Done");
    expect(statusOf(story, "Charge the card")).toBe("Working…");
    expect(statusOf(story, "Book the delivery")).toBe("Waiting");
    expect(story.summary).toBeNull();
  });

  it("explains the failure and why the undo starts", () => {
    expect(storyAt("business-failure", 9).caption).toBe(
      "The final check failed: the delivery company turned the order down.",
    );
    expect(statusOf(storyAt("business-failure", 9), "Check the order went through")).toBe("Failed");
    expect(statusOf(storyAt("business-failure", 9), "Book the delivery")).toBe("Done");
    expect(storyAt("business-failure", 10).caption).toBe(
      "The delivery company turned the order down, so the earlier steps are being undone, newest first.",
    );
  });

  it("explains each undo step and marks the step it reversed", () => {
    const story = storyAt("business-failure", 12);

    expect(story.caption).toBe("Refunded the card, because the order can't go through.");
    expect(statusOf(story, "Cancel the delivery")).toBe("Cancelled");
    expect(statusOf(story, "Release the item")).toBe("Waiting");
    expect(statusOf(story, "Book the delivery")).toBe("Undone");
    expect(statusOf(story, "Charge the card")).toBe("Undone");
    expect(statusOf(story, "Reserve the item")).toBe("Done");
  });

  it("ends with a plain summary of a safely undone order", () => {
    const story = storyAt("business-failure", 15);

    expect(story.undo.map((step) => step.status)).toEqual(["Cancelled", "Refunded", "Released"]);

    expect(story.summary).toBe(
      "The order couldn't be completed, so everything was put back: the delivery was cancelled, the card was refunded, and the item was released. Nothing was left half-done.",
    );
  });

  it("ends with a plain summary of a completed order", () => {
    expect(storyAt("happy-path", 11).summary).toBe(
      "The order went through: the item is reserved, the card was charged once, and the delivery is booked. A final check confirmed it.",
    );
  });

  it("explains a lost reply without implying a second charge", () => {
    expect(storyAt("lost-response", 5).caption).toBe(
      "The card company didn't reply. Asking whether the card was charged, instead of charging it again.",
    );
    expect(storyAt("lost-response", 6).caption).toBe(
      "The card company confirmed one charge. Carrying on.",
    );
  });

  it("explains a safe stop that needs a person", () => {
    const story = storyAt("compensation-failure", 14);

    expect(story.caption).toBe(
      "Paused: a person must confirm the refund before anything else happens.",
    );
    expect(statusOf(story, "Refund the card")).toBe("Needs a person");
    expect(story.summary).toBe(
      "Stopped safely: the refund couldn't be confirmed, so a person has to check it before anything else happens.",
    );
  });

  it("never shows technical jargon at any point of any recorded run", () => {
    for (const name of fixtures) {
      const recorded = trace(name);
      for (let cursor = 0; cursor < recorded.events.length; cursor += 1) {
        const story = plainLiveStory(projectReplay(recorded, cursor));
        const words = [
          story.caption,
          story.summary ?? "",
          ...[...story.forward, ...story.undo].flatMap((step) => [step.label, step.status]),
        ].join(" ");
        expect(words, `${name} at event ${cursor + 1}`).not.toMatch(JARGON);
      }
    }
  });
});
