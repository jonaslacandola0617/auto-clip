import { invoke } from "@tauri-apps/api/core";
import { describe, expect, it, vi } from "vitest";
import { AutoClipError, parseEnvelope, workerRequest } from "./worker";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

describe("desktop worker protocol", () => {
  it("parses a successful typed envelope", () => {
    expect(parseEnvelope<{ ready: boolean }>({ version: "1", id: "1", ok: true, data: { ready: true } })).toEqual({ ready: true });
  });

  it("converts a worker error into an AutoClipError", () => {
    expect(() => parseEnvelope({ version: "1", id: "1", ok: false, error: { code: "missing", message: "Choose a file", recoverable: true } })).toThrow(AutoClipError);
  });

  it("preserves a safe Tauri bridge diagnostic instead of hiding it", async () => {
    vi.mocked(invoke).mockRejectedValueOnce("Could not read the processing response: stream did not contain valid UTF-8; token=private-value");

    await expect(workerRequest("start_job", { type: "transcribe" })).rejects.toMatchObject({
      info: {
        code: "desktop_bridge_error",
        message: "AutoClip couldn't reach the processing worker.",
        details: "Could not read the processing response: stream did not contain valid UTF-8; token=[redacted]",
        recoverable: true,
      },
    });
  });
});
