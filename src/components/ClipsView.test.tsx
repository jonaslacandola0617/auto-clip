import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ClipsView } from "./ClipsView";
import type { Job } from "../types";

const baseJob: Job = { id: "job", type: "analyze", state: "running", progress: .4, current_stage: "Analyzing section 4 of 12", created_at: "now", started_at: "now", completed_at: null, error: null, cancellable: true, project_path: "project" };
const props = { candidates: [], clips: [], storyConcepts: [], editSequences: [], sourcePath: "source.mp4", geminiReady: true, busy: false, smartEditJob: null, onCommand: vi.fn(), onStartJob: vi.fn() };

describe("Analyze Clips outcomes", () => {
  it("shows running progress", () => {
    render(<ClipsView {...props} analysisJob={baseJob} />);
    expect(screen.getByText("Analyzing section 4 of 12")).toBeInTheDocument();
    expect(screen.getByText("40% complete")).toBeInTheDocument();
  });

  it("distinguishes zero results from failure", () => {
    const { rerender } = render(<ClipsView {...props} analysisJob={{ ...baseJob, state: "completed", progress: 1, completed_at: "now" }} />);
    expect(screen.getByText("No suitable clips were found.")).toBeInTheDocument();
    rerender(<ClipsView {...props} analysisJob={{ ...baseJob, state: "failed", error: { code: "provider", message: "Request rejected", recoverable: true }, completed_at: "now" }} />);
    expect(screen.getByText("We couldn't analyze this transcript.")).toBeInTheDocument();
    expect(screen.getByText("Request rejected")).toBeInTheDocument();
  });

  it("shows a successful persisted candidate count", () => {
    render(
      <ClipsView
        {...props}
        candidates={[{
          id: "candidate-1",
          title: "Candidate",
          start_seconds: 10,
          end_seconds: 20,
          duration_seconds: 10,
          source: "ai",
          score: 92,
          reason: "A complete moment",
          category: "story",
        }]}
        analysisJob={{ ...baseJob, state: "completed", progress: 1, completed_at: "now" }}
      />,
    );
    expect(screen.getByText("1 clip found.")).toBeInTheDocument();
  });
});
