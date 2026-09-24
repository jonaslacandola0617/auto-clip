import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TranscriptView } from "./TranscriptView";

const transcript = {
  revision_id: "revision-1",
  language: "en",
  model: "fixture",
  segments: [
    { id: "s1", start_seconds: 1, end_seconds: 3, original_text: "Opening thought", corrected_text: null, text: "Opening thought" },
    { id: "s2", start_seconds: 4, end_seconds: 6, original_text: "Useful ending", corrected_text: null, text: "Useful ending" },
  ],
};

describe("TranscriptView", () => {
  it("searches timestamped segments and submits corrections", async () => {
    const onCorrect = vi.fn().mockResolvedValue(undefined);
    render(<TranscriptView transcript={transcript} busy={false} onTranscribe={vi.fn()} onCorrect={onCorrect} onCreateClip={vi.fn().mockResolvedValue(undefined)} />);
    expect(screen.getByText("Opening thought")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Search transcript"), { target: { value: "ending" } });
    expect(await screen.findByText("Useful ending")).toBeInTheDocument();
    expect(screen.queryByText("Opening thought")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Correct" }));
    fireEvent.change(screen.getByRole("textbox", { name: "" }), { target: { value: "Better ending" } });
    fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
    expect(onCorrect).toHaveBeenCalledWith("s2", "Better ending");
  });
});
