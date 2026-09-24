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
  moment_count: number;
  story_concepts: StoryConcept[];
  edit_sequences: EditSequence[];
  analysis_revision: string | null;
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

export type OutputArtifact = { id: string; kind: string; path: string; clip_id: string | null; edit_sequence_id: string | null; warnings: string[] };

export type StoryConcept = { id: string; title: string; premise: string; hook: string; context: string; development: string; payoff: string; moment_ids: string[]; target_duration_seconds: number; explanation: string; coherence: Record<string, unknown>; integrity_considerations: string[]; status: string };
export type EditorialAction = { id: string; type: string; timeline_start: number; timeline_end: number; parameters: Record<string, unknown>; reason: string; enabled: boolean; revision: number };
export type EditSegment = { id: string; moment_id: string; source_id: string; source_in: number; source_out: number; timeline_start: number; duration_seconds: number; purpose: string; transcript_excerpt: string; order: number; actions: EditorialAction[] };
export type EditorialIntegrity = { status: "passed" | "review_required" | "failed"; checks: Array<Record<string, unknown>>; evidence_references: string[]; warnings: string[]; required_review: boolean; validator_version: string };
export type EditSequence = { id: string; story_concept_id: string; title: string; duration_seconds: number; segment_count: number; integrity: EditorialIntegrity; status: string; revision: number; preview_path: string | null; render_path: string | null; segments: EditSegment[] };

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
