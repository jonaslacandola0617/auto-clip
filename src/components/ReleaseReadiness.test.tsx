import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Doctor, Settings } from "../types";
import { HomeView } from "./HomeView";
import { SettingsView } from "./SettingsView";

const capability = { ready: true, summary: "Ready" };
const doctor: Doctor = {
  runtime: capability,
  storage: capability,
  ffmpeg: { ready: true, summary: "Bundled FFmpeg 8" },
  ffprobe: capability,
  whisper: capability,
  model: { ready: false, summary: "Base model downloads on first transcription (~500 MB)" },
  opencv: capability,
  mediapipe: capability,
  otio: capability,
  gemini: { ready: false, summary: "API key not configured" },
  acceleration: { ready: false, summary: "CPU processing will be used" },
  python: "3.12.14",
  app_version: "1.0.0-rc.1",
  paths: { data: "C:/AutoClip/Data", cache: "C:/AutoClip/Cache", models: "C:/AutoClip/Models", logs: "C:/AutoClip/Logs" },
};

const settings: Settings = {
  default_project_directory: "C:/Projects",
  default_export_directory: "",
  theme: "dark",
  whisper_model: "base",
  processing_device: "cpu",
  ai_provider: "Gemini",
  gemini_model: "gemini-fixture",
  gemini_configured: false,
};

describe("release readiness UI", () => {
  it("presents first-run readiness without treating optional downloads and Gemini as blockers", () => {
    const openSettings = vi.fn();
    render(<HomeView doctor={doctor} recents={[]} loading={false} onNew={vi.fn()} onOpen={vi.fn()} onOpenRecent={vi.fn()} onRemoveRecent={vi.fn()} onOpenSettings={openSettings} />);
    expect(screen.getByRole("heading", { name: "Ready to create" })).toBeInTheDocument();
    expect(screen.getByText("AI story analysis (optional)")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open settings" }));
    expect(openSettings).toHaveBeenCalledOnce();
  });

  it("stores a Gemini key through the secure credential command and clears the field", async () => {
    const storeKey = vi.fn().mockResolvedValue(undefined);
    render(<SettingsView settings={settings} doctor={doctor} onSave={vi.fn()} onSetGeminiApiKey={storeKey} onClearGeminiApiKey={vi.fn()} onClearCache={vi.fn()} onExportDiagnostics={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "AI" }));
    const input = screen.getByLabelText(/Gemini API key/) as HTMLInputElement;
    expect(input.type).toBe("password");
    fireEvent.change(input, { target: { value: "secret-fixture" } });
    fireEvent.click(screen.getByRole("button", { name: "Save API key" }));
    await waitFor(() => expect(storeKey).toHaveBeenCalledWith("secret-fixture"));
    await waitFor(() => expect(input.value).toBe(""));
    expect(screen.getByRole("status")).toHaveTextContent("stored securely");
  });
});
