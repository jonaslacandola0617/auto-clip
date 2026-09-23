import { describe, expect, it } from "vitest";
import { AutoClipError, parseEnvelope } from "./worker";

describe("desktop worker protocol", () => {
  it("parses a successful typed envelope", () => {
    expect(parseEnvelope<{ ready: boolean }>({ version: "1", id: "1", ok: true, data: { ready: true } })).toEqual({ ready: true });
  });

  it("converts a worker error into an AutoClipError", () => {
    expect(() => parseEnvelope({ version: "1", id: "1", ok: false, error: { code: "missing", message: "Choose a file", recoverable: true } })).toThrow(AutoClipError);
  });
});

