export type Capability = { ready: boolean; summary: string };

export type Doctor = {
  ffmpeg: Capability;
  ffprobe: Capability;
  whisper: Capability;
  opencv: Capability;
  mediapipe: Capability;
  otio: Capability;
  gemini: Capability;
  acceleration: Capability;
  python: string;
};

export type SourceSummary = {
  id: string;
  path: string;
  filename: string;
  available: boolean;
  duration_seconds: number;
  width: number;
  height: number;
  frame_rate: number;
  frame_rate_label: string;
  codec: string;
  has_audio: boolean;
  preview_path: string | null;
};

export type ProjectState = {
  path: string;
  directory: string;
  id: string;
  name: string;
  schema_version: string;
  status: "needs_source" | "source_missing" | "needs_transcript" | "needs_analysis" | "ready";
  source: SourceSummary | null;
  transcript_count: number;
  candidate_count: number;
  timeline_count: number;
  transcript: Transcript | null;
  candidates: ClipCandidate[];
  clips: ProjectClip[];
  outputs: OutputArtifact[];
};

export type TranscriptSegment = {
  id: string;
  start_seconds: number;
  end_seconds: number;
  original_text: string;
  corrected_text: string | null;
  text: string;
};

export type Transcript = { revision_id: string; language: string; model: string; segments: TranscriptSegment[] };

export type ClipCandidate = {
  id: string;
  title: string;
  start_seconds: number;
  end_seconds: number;
  duration_seconds: number;
  source: "ai";
  score: number;
  category: string;
  reason: string;
};

export type ManualCrop = { enabled: boolean; crop_x: number; crop_y: number; scale: number };

export type ProjectClip = {
  id: string;
  title: string;
  source: "ai" | "manual";
  candidate_id: string | null;
  selected: boolean;
  start_seconds: number;
  end_seconds: number;
  duration_seconds: number;
  framing_mode: "auto" | "manual";
  manual_crop: ManualCrop;
  captions_enabled: boolean;
  caption_preset: "clean" | "bold_social" | "word_highlight";
  has_reframe: boolean;
  caption_cue_count: number;
  preview_path: string | null;
  render_path: string | null;
  revision: number;
};

export type OutputArtifact = { id: string; kind: string; path: string; clip_id: string | null; warnings: string[] };

export type RecentProject = { path: string; name: string; last_opened: string; available: boolean };

export type Job = {
  id: string;
  type: string;
  state: "queued" | "running" | "completed" | "failed" | "cancelled";
  progress: number;
  current_stage: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  error: { code: string; message: string; recoverable: boolean } | null;
  cancellable: boolean;
  project_path: string | null;
};

export type Settings = {
  default_project_directory: string;
  default_export_directory: string;
  theme: "dark" | "light";
  whisper_model: "tiny" | "base" | "small" | "medium";
  processing_device: "cpu";
  ai_provider: "Gemini";
  gemini_model: string;
  gemini_configured: boolean;
};

export type WorkerError = { code: string; message: string; recoverable: boolean; details?: string };
export type Envelope<T> = { version: "1"; id: string; ok: true; data: T } | { version: "1"; id: string; ok: false; error: WorkerError };
