import { invoke } from "@tauri-apps/api/core";
import type { Envelope, WorkerError } from "../types";

let requestSequence = 0;

export class AutoClipError extends Error {
  constructor(public readonly info: WorkerError) {
    super(info.message);
    this.name = "AutoClipError";
  }
}

export function parseEnvelope<T>(value: unknown): T {
  if (!value || typeof value !== "object") throw new Error("Invalid worker response");
  const envelope = value as Envelope<T>;
  if (envelope.version !== "1" || typeof envelope.ok !== "boolean") throw new Error("Unsupported worker response");
  if (!envelope.ok) throw new AutoClipError(envelope.error);
  return envelope.data;
}

function diagnosticMessage(reason: unknown): string {
  const raw = reason instanceof Error ? reason.message : typeof reason === "string" ? reason : "Unknown desktop bridge error";
  return raw
    .replace(/((?:api[_-]?key|token|secret|authorization)\s*[:=]\s*)[^\s,;]+/gi, "$1[redacted]")
    .replace(/([?&](?:key|token|secret)=)[^&\s]+/gi, "$1[redacted]");
}

export async function workerRequest<T>(command: string, payload: Record<string, unknown> = {}): Promise<T> {
  const id = `desktop-${Date.now()}-${++requestSequence}`;
  try {
    const response = await invoke<unknown>("worker_request", { request: { version: "1", id, command, payload } });
    return parseEnvelope<T>(response);
  } catch (reason) {
    if (reason instanceof AutoClipError) throw reason;
    throw new AutoClipError({
      code: "desktop_bridge_error",
      message: "AutoClip couldn't reach the processing worker.",
      details: diagnosticMessage(reason),
      recoverable: true,
    });
  }
}
