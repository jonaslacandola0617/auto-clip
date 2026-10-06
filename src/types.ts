export type Capability = { ready: boolean; summary: string };

export type Doctor = {
  runtime: Capability;
  storage: Capability;
  ffmpeg: Capability;
  ffprobe: Capability;
  whisper: Capability;
  model: Capability;
  opencv: Capability;
  mediapipe: Capability;
  otio: Capability;
  gemini: Capability;
  acceleration: Capability;
  python: string;
  app_version: string;
  paths: { data: string; cache: string; models: string; logs: string };
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
  workflow_profiles: WorkflowProfile[];
  campaign_profiles: CampaignProfile[];
  production_runs: ProductionRun[];
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

export type StoryConcept = { id: string; title: string; premise: string; hook: string; context: string; development: string; payoff: string; moment_ids: string[]; target_duration_seconds: number; explanation: string; coherence: Record<string, unknown>; integrity_considerations: string[]; status: string; central_topic: string; viewer_premise: string; moment_rationales: Record<string, string>; understandable_without_source: boolean };
export type EditorialAction = { id: string; type: string; timeline_start: number; timeline_end: number; parameters: Record<string, unknown>; reason: string; enabled: boolean; revision: number };
export type EditSegment = { id: string; moment_id: string; source_id: string; source_in: number; source_out: number; timeline_start: number; duration_seconds: number; purpose: string; transcript_excerpt: string; order: number; actions: EditorialAction[] };
export type EditorialIntegrity = { status: "passed" | "review_required" | "failed"; checks: Array<Record<string, unknown>>; evidence_references: string[]; warnings: string[]; required_review: boolean; validator_version: string };
export type EditorialReview = { topic: string; viewer_premise: string; hook_present: boolean; self_contained: boolean; payoff_present: boolean; coherence_score: number; segment_roles: Array<{ segment_id: string; moment_id: string; role: string; why_it_belongs: string }>; relevant_entities: string[]; central_tension: string; resolution: string; problems: string[]; accepted: boolean; rebuild_attempts: number; reviewer_version: string };
export type VisualPlanSummary = { id: string; revision: number; framing_mode: "auto" | "fixed"; visual_emphasis: "automatic" | "off"; caption_preset: ProjectClip["caption_preset"]; caption_position: "auto" | "upper" | "center" | "lower"; warnings: string[]; actions: EditorialAction[]; layouts: Array<{ edit_segment_id: string; position: string; reason: string }> };
export type EnhancementItem = { id: string; timeline_start: number; timeline_end?: number; duration?: number; text?: string; asset_id?: string; reason?: string; purpose?: string; enabled: boolean };
export type EnhancementPlanSummary = { id: string; revision: number; status: string; warnings: string[]; broll_items: EnhancementItem[]; graphic_items: EnhancementItem[]; sound_cues: EnhancementItem[]; music_track: ({ asset_id: string; reason: string; enabled: boolean } | null) };
export type EditSequence = { id: string; story_concept_id: string; title: string; duration_seconds: number; segment_count: number; integrity: EditorialIntegrity; editorial_review: EditorialReview | null; status: string; revision: number; preview_path: string | null; render_path: string | null; visual_plan?: VisualPlanSummary | null; enhancement_plan?: EnhancementPlanSummary | null; segments: EditSegment[] };
export type WorkflowProfile = { id: string; name: string; revision: number; generation_mode: "concepts_only" | "prepare_previews"; target_platform: string; min_duration_seconds: number; max_duration_seconds: number; desired_output_count: number; pacing: "relaxed" | "balanced" | "fast"; hook_priority: string; story_style: string; framing: string; visual_emphasis: string; caption_preset: ProjectClip["caption_preset"]; enhancement_policy: "off" | "restrained" | "automatic"; music_policy: "off" | "optional"; export_defaults: string[]; campaign_profile_id: string | null; version: string };
export type CampaignProfile = { id: string; name: string; revision: number; creator: string; target_platform: string; min_duration_seconds: number; max_duration_seconds: number; required_handle: string; required_cta: string; required_text: string[]; hashtags: string[]; watermark_required: boolean; forbidden_terms: string[]; content_notes: string; target_deliverables: number; export_naming: string; version: string };
export type CampaignValidation = { state: "passed_checks" | "needs_review" | "failed_checks"; checks: Array<{ name: string; state: string; detail: string }>; warnings: string[] };
export type ProductionRun = { id: string; workflow_profile_id: string; workflow_profile_revision: number; campaign_profile_id: string | null; campaign_profile_revision: number | null; requested_count: number; generated_edit_ids: string[]; accepted_edit_ids: string[]; rejected_edit_ids: string[]; selected_edit_ids: string[]; edit_states: Record<string, { state: string; stage: string; error: string | null }>; campaign_validations: Record<string, CampaignValidation>; status: string; warnings: string[]; errors: string[]; output_package_path: string | null; summary: { requested: number; produced: number; approved: number; rendered: number; failed: number; needs_review: number } };

export type RecentProject = { path: string; name: string; last_opened: string; available: boolean };

export type Job = {
  id: string;
  type: string;
  state: "queued" | "waiting" | "running" | "cancelling" | "completed" | "failed" | "cancelled";
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
