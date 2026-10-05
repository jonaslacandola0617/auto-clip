import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ClipsView } from "./ClipsView";
import type { Job } from "../types";

vi.mock("@tauri-apps/api/core", () => ({ convertFileSrc: (path: string) => path, invoke: vi.fn().mockImplementation((_command: string, payload: { path: string }) => Promise.resolve(payload.path)) }));

const baseJob: Job = { id: "job", type: "analyze", state: "running", progress: .4, current_stage: "Analyzing section 4 of 12", created_at: "now", started_at: "now", completed_at: null, error: null, cancellable: true, project_path: "project" };
const props = { candidates: [], clips: [], storyConcepts: [], editSequences: [], workflowProfiles: [], campaignProfiles: [], productionRuns: [], sourcePath: "source.mp4", geminiReady: true, busy: false, smartEditJob: null, onCommand: vi.fn(), onStartJob: vi.fn(), onRelinkEnhancementAsset: vi.fn() };

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

  it("does not present a failed editorial review as a ready Smart Edit", () => {
    const view = render(<ClipsView {...props} analysisJob={null} smartEditJob={{ ...baseJob, type: "smart_edit", state: "completed", progress: 1, completed_at: "now" }} editSequences={[{
      id: "rejected", story_concept_id: "story", title: "Weak montage", duration_seconds: 20, segment_count: 2,
      integrity: { status: "passed", checks: [], evidence_references: [], warnings: [], required_review: false, validator_version: "test" },
      editorial_review: { topic: "", viewer_premise: "", hook_present: false, self_contained: false, payoff_present: false, coherence_score: .2, segment_roles: [], relevant_entities: [], central_tension: "", resolution: "", problems: ["No topic"], accepted: false, rebuild_attempts: 0, reviewer_version: "test" },
      status: "rejected", revision: 1, preview_path: null, render_path: null, segments: [],
    }]} />);
    const smartButton = [...view.container.querySelectorAll("button")].find((button) => button.textContent === "Smart Edits");
    expect(smartButton).toBeTruthy();
    fireEvent.click(smartButton!);
    expect(view.getByText("AutoClip couldn't build a coherent Smart Edit from these moments.")).toBeInTheDocument();
    expect(view.queryByText("Weak montage")).not.toBeInTheDocument();
  });

  it("shows bounded visual treatment controls and preserves action toggles", () => {
    const onCommand = vi.fn();
    const view = render(<ClipsView {...props} onCommand={onCommand} analysisJob={null} editSequences={[{
      id: "ready", story_concept_id: "story", title: "Race", duration_seconds: 8, segment_count: 2,
      integrity: { status: "passed", checks: [], evidence_references: [], warnings: [], required_review: false, validator_version: "test" },
      editorial_review: { topic: "race", viewer_premise: "Speed races Tyreek", hook_present: true, self_contained: true, payoff_present: true, coherence_score: 1, segment_roles: [{ segment_id: "a", moment_id: "m1", role: "hook", why_it_belongs: "opens" }, { segment_id: "b", moment_id: "m2", role: "payoff", why_it_belongs: "resolves" }], relevant_entities: ["Tyreek"], central_tension: "who wins", resolution: "a deal", problems: [], accepted: true, rebuild_attempts: 0, reviewer_version: "test" },
      status: "ready", revision: 1, preview_path: null, render_path: null,
      visual_plan: { id: "visual", revision: 1, framing_mode: "auto", visual_emphasis: "automatic", caption_preset: "word_highlight", caption_position: "auto", warnings: [], layouts: [], actions: [{ id: "punch", type: "punch_in", timeline_start: 1, timeline_end: 2, parameters: { scale: 1.1 }, reason: "hook emphasis", enabled: true, revision: 1 }] },
      enhancement_plan: { id: "enhancement", revision: 1, status: "ready", warnings: [], broll_items: [], graphic_items: [{ id: "graphic", timeline_start: .5, timeline_end: 2, text: "40-YARD DASH", enabled: true }], sound_cues: [], music_track: null },
      segments: [{ id: "a", moment_id: "m1", source_id: "media", source_in: 10, source_out: 14, timeline_start: 0, duration_seconds: 4, purpose: "hook", transcript_excerpt: "Race me", order: 0, actions: [] }, { id: "b", moment_id: "m2", source_id: "media", source_in: 20, source_out: 24, timeline_start: 4, duration_seconds: 4, purpose: "payoff", transcript_excerpt: "Deal", order: 1, actions: [] }],
    }]} />);
    const scoped = within(view.container);
    fireEvent.click(scoped.getByRole("button", { name: "Smart Edits" }));
    fireEvent.click(scoped.getByRole("button", { name: /Race.*passed/ }));
    fireEvent.click(scoped.getByRole("button", { name: "Visual" }));
    expect(within(scoped.getByRole("group", { name: "Smart Edit framing" })).getByRole("button", { name: "Auto" })).toHaveAttribute("aria-pressed", "true");
    expect(within(scoped.getByRole("group", { name: "Visual emphasis" })).getByRole("button", { name: "Automatic" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(scoped.getByRole("button", { name: /Punch-in/ }));
    expect(onCommand).toHaveBeenCalledWith("update_visual_plan", expect.objectContaining({ action_id: "punch", enabled: false }));
    fireEvent.click(scoped.getByRole("button", { name: "Enhancements" }));
    fireEvent.click(scoped.getByRole("checkbox", { name: /Graphic/ }));
    expect(onCommand).toHaveBeenCalledWith("update_enhancement_plan", expect.objectContaining({ item_id: "graphic", enabled: false }));
  });

  it("renders a Phase 3A production summary and generation action", () => {
    const view = render(<ClipsView {...props} analysisJob={null} workflowProfiles={[{ id: "profile", name: "Fast Shorts", revision: 2, generation_mode: "concepts_only", target_platform: "shorts", min_duration_seconds: 15, max_duration_seconds: 45, desired_output_count: 3, pacing: "fast", hook_priority: "strong", story_style: "self-contained", framing: "automatic", visual_emphasis: "restrained", caption_preset: "word_highlight", enhancement_policy: "restrained", music_policy: "off", export_defaults: ["mp4"], campaign_profile_id: null, version: "phase3a-v1" }]} productionRuns={[{ id: "run", workflow_profile_id: "profile", workflow_profile_revision: 2, campaign_profile_id: null, campaign_profile_revision: null, requested_count: 3, generated_edit_ids: [], accepted_edit_ids: [], rejected_edit_ids: [], selected_edit_ids: [], edit_states: {}, campaign_validations: {}, status: "review", warnings: ["Requested 3; produced 1 qualified distinct edit."], errors: [], output_package_path: null, summary: { requested: 3, produced: 1, approved: 0, rendered: 0, failed: 0, needs_review: 0 } }]} />);
    const scoped = within(view.container);
    fireEvent.click(scoped.getByRole("button", { name: "Smart Edits" }));
    expect(scoped.getByText("Qualified")).toBeInTheDocument();
    expect(scoped.getByRole("button", { name: "Generate distinct edits" })).toBeInTheDocument();
    expect(scoped.getByText(/produced 1 qualified distinct edit/i)).toBeInTheDocument();
  });
});
