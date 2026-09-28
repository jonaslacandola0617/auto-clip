import { useEffect, useState } from "react";
import type { CampaignProfile, ClipCandidate, EditSequence, EditSegment, Job, ProductionRun, ProjectClip, StoryConcept, WorkflowProfile } from "../types";
import { ActionChip, Badge, Button, EmptyState, Input, Panel, SegmentedControl, Select, Status } from "./Ui";
import { VideoPreview } from "./VideoPreview";

type CommandHandler = (command: string, payload: Record<string, unknown>) => Promise<void>;
type JobHandler = (type: string, payload?: Record<string, unknown>) => Promise<void>;
type Mode = "highlights" | "smart";

function clock(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${Math.floor(seconds % 60).toString().padStart(2, "0")}`;
}

const PURPOSE_LABEL: Record<string, string> = { hook: "Hook", context: "Context", development: "Development", payoff: "Payoff", reaction: "Reaction" };
function purposeLabel(purpose: string) { return PURPOSE_LABEL[purpose] ?? purpose.replace(/_/g, " "); }

const ACTION_LABEL: Record<string, string> = { hard_cut: "Cut", jump_cut: "Jump cut", trim_silence: "Trim silence", punch_in: "Punch-in", framing_change: "Reframe", caption_emphasis: "Caption emphasis", short_hold: "Hold" };
function actionLabel(type: string) { return ACTION_LABEL[type] ?? type.replace(/_/g, " "); }

const CAPTION_PRESETS: Array<{ value: ProjectClip["caption_preset"]; label: string }> = [
  { value: "clean", label: "Clean" },
  { value: "bold_social", label: "Bold" },
  { value: "word_highlight", label: "Word Highlight" },
];

export function ClipsView({ candidates, clips, storyConcepts, editSequences, workflowProfiles, campaignProfiles, productionRuns, sourcePath, geminiReady, busy, analysisJob, smartEditJob, onCommand, onStartJob, onRelinkEnhancementAsset }: {
  candidates: ClipCandidate[];
  clips: ProjectClip[];
  storyConcepts: StoryConcept[];
  editSequences: EditSequence[];
  workflowProfiles: WorkflowProfile[];
  campaignProfiles: CampaignProfile[];
  productionRuns: ProductionRun[];
  sourcePath: string;
  geminiReady: boolean;
  busy: boolean;
  analysisJob: Job | null;
  smartEditJob: Job | null;
  onCommand: CommandHandler;
  onStartJob: JobHandler;
  onRelinkEnhancementAsset: (editSequenceId: string, assetId: string) => Promise<void>;
}) {
  const [mode, setMode] = useState<Mode>("highlights");
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [focus, setFocus] = useState<{ type: "candidate" | "clip"; id: string } | null>(null);
  const [editingSequenceId, setEditingSequenceId] = useState<string | null>(null);

  const focusedCandidate = focus?.type === "candidate" ? candidates.find((c) => c.id === focus.id) ?? null : null;
  const focusedClip = focus?.type === "clip" ? clips.find((c) => c.id === focus.id) ?? null : null;
  const editingSequence = editSequences.find((sequence) => sequence.id === editingSequenceId) ?? null;
  const readySequences = editSequences.filter((sequence) => sequence.status === "ready" && sequence.editorial_review?.accepted);

  return <div className="clips-layout">
    <div>
      <SegmentedControl ariaLabel="Generation mode" value={mode} onChange={setMode} options={[{ value: "highlights", label: "Highlight Clips" }, { value: "smart", label: "Smart Edits" }]} />

      {mode === "highlights" ? <>
        <Panel title="AI candidates" action={<Button variant="primary" disabled={!geminiReady || busy} onClick={() => onStartJob("analyze")}>{busy ? "Analyzing…" : "Analyze clips"}</Button>}>
          {!geminiReady ? <div className="notice notice--warning"><span>Gemini isn't configured yet. Manual clip creation remains available in Transcript.</span></div> : null}
          <AnalysisOutcome job={analysisJob} count={candidates.length} noun="clips" />
          {candidates.length ? <div className="scan-list">{candidates.filter((c) => !dismissed.has(c.id)).map((candidate) => (
            <CandidateRow
              key={candidate.id}
              candidate={candidate}
              focused={focus?.type === "candidate" && focus.id === candidate.id}
              onFocus={() => setFocus({ type: "candidate", id: candidate.id })}
              onAccept={() => onCommand("create_clip_from_candidate", { candidate_id: candidate.id })}
              onDismiss={() => setDismissed((current) => new Set(current).add(candidate.id))}
            />
          ))}</div> : <EmptyState title="No AI candidates yet" description="Analyze the transcript with Gemini, or create clips manually from transcript segments." />}
        </Panel>

        <Panel title="Project clips">
          {clips.length ? <div className="scan-list">{clips.map((clip) => (
            <ClipRow
              key={clip.id}
              clip={clip}
              focused={focus?.type === "clip" && focus.id === clip.id}
              onOpen={() => setFocus({ type: "clip", id: clip.id })}
              onSelect={(selected) => onCommand("select_clip", { clip_id: clip.id, selected })}
              onDelete={() => onCommand("delete_clip", { clip_id: clip.id })}
            />
          ))}</div> : <EmptyState title="No clips yet" description="Create a manual clip in Transcript or accept an AI candidate." />}
        </Panel>
      </> : <>
        <Panel title="Smart Edits" action={<Button variant="primary" disabled={!geminiReady || busy} onClick={() => onStartJob("smart_edit")}>{busy ? "Building…" : "Find Smart Edits"}</Button>}>
          <AnalysisOutcome job={smartEditJob} count={readySequences.length} noun="Smart Edits" zeroTitle="AutoClip couldn't build a coherent Smart Edit from these moments." />
          {readySequences.length ? <div className="scan-list">{readySequences.map((sequence) => <SmartEditCard key={sequence.id} sequence={sequence} story={storyConcepts.find((item) => item.id === sequence.story_concept_id)} onOpen={() => setEditingSequenceId(sequence.id)} />)}</div> : <EmptyState title="No ready Smart Edits" description="Only edits that pass the existing hook, context, payoff, and integrity gates appear here." />}
        </Panel>
        <ProductionSection profiles={workflowProfiles} campaigns={campaignProfiles} runs={productionRuns} sequences={editSequences} busy={busy} onCommand={onCommand} onStartJob={onStartJob} onOpenEdit={setEditingSequenceId} />
      </>}
    </div>

    {mode === "smart"
      ? (editingSequence ? <SmartEditEditor sequence={editingSequence} sourcePath={sourcePath} onClose={() => setEditingSequenceId(null)} onCommand={onCommand} onStartJob={onStartJob} onRelinkEnhancementAsset={onRelinkEnhancementAsset} /> : <Panel title="Smart Edit review"><EmptyState title="Choose a Smart Edit" description="Review its ordered story structure, visual treatment, enhancements, and output actions." /></Panel>)
      : (focusedClip ? <ClipEditor key={`${focusedClip.id}-${focusedClip.revision}`} clip={focusedClip} sourcePath={sourcePath} onClose={() => setFocus(null)} onCommand={onCommand} onStartJob={onStartJob} />
        : focusedCandidate ? <CandidateReview candidate={focusedCandidate} onClose={() => setFocus(null)} onAccept={() => onCommand("create_clip_from_candidate", { candidate_id: focusedCandidate.id })} />
        : <Panel title="Clip editor"><EmptyState title="Choose a clip" description="Preview, trim, frame, caption, and render a project clip." /></Panel>)}
  </div>;
}

function ProductionSection({ profiles, campaigns, runs, sequences, busy, onCommand, onStartJob, onOpenEdit }: {
  profiles: WorkflowProfile[];
  campaigns: CampaignProfile[];
  runs: ProductionRun[];
  sequences: EditSequence[];
  busy: boolean;
  onCommand: CommandHandler;
  onStartJob: JobHandler;
  onOpenEdit: (id: string) => void;
}) {
  const [profileId, setProfileId] = useState("");
  const [campaignId, setCampaignId] = useState("");
  const [requestedCount, setRequestedCount] = useState(3);
  const [selected, setSelected] = useState<string[]>([]);
  const [showProfile, setShowProfile] = useState(false);
  const [showCampaign, setShowCampaign] = useState(false);
  const [profileName, setProfileName] = useState("Fast Shorts");
  const [minDuration, setMinDuration] = useState(15);
  const [maxDuration, setMaxDuration] = useState(45);
  const [campaignName, setCampaignName] = useState("Campaign Delivery");
  const [requiredHandle, setRequiredHandle] = useState("");
  const [requiredCta, setRequiredCta] = useState("");
  const [requiredText, setRequiredText] = useState("");
  const [watermarkRequired, setWatermarkRequired] = useState(false);
  const [forbiddenTerms, setForbiddenTerms] = useState("");
  const effectiveProfileId = profileId || profiles[0]?.id || "";
  const run = runs.at(-1);
  useEffect(() => setSelected([]), [run?.id]);
  const toggle = (id: string) => setSelected((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  const chooseForRun = async () => {
    if (!run || !selected.length) return;
    await onCommand("update_production_run", { production_run_id: run.id, operation: "select", edit_ids: selected });
  };

  return <Panel title="Production" className="production-panel" action={<Button variant="quiet" onClick={() => setShowProfile((value) => !value)}>{showProfile ? "Close profile" : profiles.length ? "New profile" : "Create profile"}</Button>}>
    <p className="muted">Generate and deliver several distinct edits without configuring each one independently.</p>

    {showProfile || !profiles.length ? <div className="production-form">
      <label className="field"><span className="field__label">Profile name</span><Input value={profileName} onChange={(event) => setProfileName(event.target.value)} /></label>
      <div className="timing-grid"><label className="field"><span className="field__label">Minimum seconds</span><Input type="number" min="5" value={minDuration} onChange={(event) => setMinDuration(Number(event.target.value))} /></label><label className="field"><span className="field__label">Maximum seconds</span><Input type="number" min={minDuration} value={maxDuration} onChange={(event) => setMaxDuration(Number(event.target.value))} /></label><label className="field"><span className="field__label">Outputs</span><Input type="number" min="1" max="20" value={requestedCount} onChange={(event) => setRequestedCount(Number(event.target.value))} /></label></div>
      <Button variant="primary" onClick={async () => { await onCommand("save_workflow_profile", { name: profileName, generation_mode: "concepts_only", target_platform: "shorts", min_duration_seconds: minDuration, max_duration_seconds: maxDuration, desired_output_count: requestedCount, pacing: "fast", hook_priority: "strong", story_style: "self-contained", framing: "automatic", visual_emphasis: "restrained", caption_preset: "word_highlight", enhancement_policy: "restrained", music_policy: "off", export_defaults: ["mp4", "srt", "otio", "premiere_xml"] }); setShowProfile(false); }}>Save workflow profile</Button>
    </div> : null}

    {profiles.length ? <div className="production-setup">
      <label className="field"><span className="field__label">Workflow profile</span><Select value={effectiveProfileId} onChange={(event) => setProfileId(event.target.value)}>{profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name} · revision {profile.revision}</option>)}</Select></label>
      <label className="field"><span className="field__label">Requested outputs</span><Input type="number" min="1" max="20" value={requestedCount} onChange={(event) => setRequestedCount(Number(event.target.value))} /></label>
      <label className="field"><span className="field__label">Campaign requirements</span><Select value={campaignId} onChange={(event) => setCampaignId(event.target.value)}><option value="">None</option>{campaigns.map((campaign) => <option key={campaign.id} value={campaign.id}>{campaign.name} · revision {campaign.revision}</option>)}</Select></label>
      <div className="editor-actions"><Button variant="quiet" onClick={() => setShowCampaign((value) => !value)}>{showCampaign ? "Close campaign" : "New campaign"}</Button><Button variant="primary" disabled={busy || !effectiveProfileId} onClick={() => onCommand("create_production_run", { workflow_profile_id: effectiveProfileId, campaign_profile_id: campaignId || undefined, requested_count: requestedCount })}>New production run</Button></div>
    </div> : null}

    {showCampaign ? <div className="production-form"><label className="field"><span className="field__label">Campaign name</span><Input value={campaignName} onChange={(event) => setCampaignName(event.target.value)} /></label><label className="field"><span className="field__label">Required handle</span><Input value={requiredHandle} onChange={(event) => setRequiredHandle(event.target.value)} placeholder="Optional" /></label><label className="field"><span className="field__label">Required call to action</span><Input value={requiredCta} onChange={(event) => setRequiredCta(event.target.value)} placeholder="Optional" /></label><label className="field"><span className="field__label">Required text</span><Input value={requiredText} onChange={(event) => setRequiredText(event.target.value)} placeholder="Comma-separated phrases" /></label><label className="field checkbox-field"><input type="checkbox" checked={watermarkRequired} onChange={(event) => setWatermarkRequired(event.target.checked)} /><span>Require watermark review</span></label><label className="field"><span className="field__label">Forbidden terms</span><Input value={forbiddenTerms} onChange={(event) => setForbiddenTerms(event.target.value)} placeholder="Comma-separated" /></label><Button onClick={async () => { await onCommand("save_campaign_profile", { name: campaignName, target_platform: "shorts", min_duration_seconds: minDuration, max_duration_seconds: maxDuration, required_handle: requiredHandle, required_cta: requiredCta, required_text: requiredText.split(",").map((item) => item.trim()).filter(Boolean), watermark_required: watermarkRequired, forbidden_terms: forbiddenTerms.split(",").map((item) => item.trim()).filter(Boolean), target_deliverables: requestedCount }); setShowCampaign(false); }}>Save campaign profile</Button></div> : null}

    {run ? <div className="production-run">
      <div className="production-summary"><div><span>Requested</span><strong>{run.summary.requested}</strong></div><div><span>Qualified</span><strong>{run.summary.produced}</strong></div><div><span>Approved</span><strong>{run.summary.approved}</strong></div><div><span>Rendered</span><strong>{run.summary.rendered}</strong></div><div><span>Needs review</span><strong>{run.summary.needs_review}</strong></div></div>
      <div className="production-run__heading"><div><strong>{profiles.find((item) => item.id === run.workflow_profile_id)?.name ?? "Production run"}</strong><span className="muted">Profile revision {run.workflow_profile_revision} · {run.status.replace(/_/g, " ")}</span></div><Button disabled={busy} onClick={() => onStartJob("production_generate", { production_run_id: run.id })}>{run.generated_edit_ids.length ? "Regenerate qualified set" : "Generate distinct edits"}</Button></div>
      {run.warnings.map((warning) => <div className="notice notice--warning" key={warning}><span>{warning}</span></div>)}
      {run.errors.length ? <div className="notice notice--warning"><strong>Some edits need attention</strong>{run.errors.map((error) => <span key={error}>{error}</span>)}</div> : null}
      {run.generated_edit_ids.length ? <div className="production-edits">{run.generated_edit_ids.map((id) => { const sequence = sequences.find((item) => item.id === id); if (!sequence) return null; const state = run.edit_states[id]?.state ?? "review"; const validation = run.campaign_validations[id]; return <article className="production-edit" key={id}><label><input type="checkbox" checked={selected.includes(id)} onChange={() => toggle(id)} /><span><strong>{sequence.title}</strong><small>{sequence.duration_seconds.toFixed(1)}s · {state.replace(/_/g, " ")}{validation ? ` · campaign ${validation.state.replace(/_/g, " ")}` : ""}</small></span></label><Button variant="quiet" onClick={() => onOpenEdit(id)}>Review</Button>{run.edit_states[id]?.error ? <p className="error-copy">{run.edit_states[id].error}</p> : null}</article>; })}</div> : <EmptyState title="No generated edits yet" description="Generate a run to select distinct Smart Edits that pass the existing editorial gates." />}
      {run.generated_edit_ids.length ? <div className="production-actions"><Button variant="quiet" onClick={() => setSelected(run.generated_edit_ids)}>Select all</Button><Button disabled={!selected.length} onClick={() => onCommand("update_production_run", { production_run_id: run.id, operation: "approve", edit_ids: selected })}>Approve</Button><Button disabled={!selected.length} onClick={() => onCommand("update_production_run", { production_run_id: run.id, operation: "reject", edit_ids: selected })}>Reject</Button><Button disabled={!selected.length} onClick={() => onCommand("bulk_production_action", { production_run_id: run.id, operation: "caption_preset", edit_ids: selected, caption_preset: "word_highlight" })}>Apply captions</Button><Button disabled={!selected.length} onClick={() => onCommand("bulk_production_action", { production_run_id: run.id, operation: "enhancement_policy", edit_ids: selected, enhancement_policy: "off" })}>Turn enhancements off</Button><Button disabled={!selected.length || busy} onClick={async () => { await chooseForRun(); await onStartJob("batch_preview", { production_run_id: run.id }); }}>Queue previews</Button><Button variant="primary" disabled={!selected.length || busy} onClick={async () => { await chooseForRun(); await onStartJob("batch_render", { production_run_id: run.id }); }}>Render selected</Button><Button disabled={busy || !run.generated_edit_ids.length} onClick={() => onStartJob("production_package", { production_run_id: run.id })}>Create package</Button></div> : null}
      {run.output_package_path ? <p className="muted">Package ready: {run.output_package_path}</p> : null}
    </div> : <EmptyState title="No production run" description="Choose a workflow profile and start a run when you need several deliverables." />}
  </Panel>;
}

function AnalysisOutcome({ job, count, noun, zeroTitle }: { job: Job | null; count: number; noun: string; zeroTitle?: string }) {
  if (!job) return null;
  if (job.state === "queued" || job.state === "running") return <div className="notice"><strong>{job.current_stage}</strong><span>{Math.round(job.progress * 100)}% complete</span></div>;
  if (job.state === "failed") return <div className="notice notice--warning"><strong>We couldn't analyze this transcript.</strong><span>{job.error?.message ?? "Try again or create a clip manually."}</span></div>;
  if (job.state === "completed" && count === 0) return <div className="notice notice--warning"><strong>{zeroTitle ?? `No suitable ${noun} were found.`}</strong><span>Try again, create a clip manually, or use the other generation mode.</span></div>;
  if (job.state === "completed") {
    const displayNoun = count === 1 && noun.endsWith("s") ? noun.slice(0, -1) : noun;
    return <div className="notice"><strong>{count} {displayNoun} found.</strong></div>;
  }
  return null;
}

function CandidateRow({ candidate, focused, onFocus, onAccept, onDismiss }: { candidate: ClipCandidate; focused: boolean; onFocus: () => void; onAccept: () => void; onDismiss: () => void }) {
  return (
    <article className={focused ? "scan-row scan-row--focused" : "scan-row"} onClick={onFocus}>
      <div className="scan-row__main"><strong>{candidate.title}</strong><span className="scan-row__meta">{clock(candidate.start_seconds)}–{clock(candidate.end_seconds)} · {candidate.duration_seconds.toFixed(1)}s</span></div>
      <div className="scan-row__badges"><Badge>{candidate.category}</Badge><Badge>Score {candidate.score}</Badge></div>
      <div className="scan-row__actions"><Button variant="primary" onClick={(event) => { event.stopPropagation(); onAccept(); }}>Accept</Button><Button variant="quiet" onClick={(event) => { event.stopPropagation(); onDismiss(); }}>Dismiss</Button></div>
    </article>
  );
}

function CandidateReview({ candidate, onClose, onAccept }: { candidate: ClipCandidate; onClose: () => void; onAccept: () => void }) {
  return (
    <Panel title="Candidate" action={<Button variant="quiet" onClick={onClose}>Close</Button>}>
      <h3>{candidate.title}</h3>
      <p className="muted">{clock(candidate.start_seconds)}–{clock(candidate.end_seconds)} · {candidate.duration_seconds.toFixed(1)}s</p>
      <div className="button-row"><Badge>{candidate.category}</Badge><Badge>Score {candidate.score}</Badge></div>
      <p>{candidate.reason}</p>
      <Button variant="primary" onClick={onAccept}>Accept and create clip</Button>
    </Panel>
  );
}

function ClipRow({ clip, focused, onOpen, onSelect, onDelete }: { clip: ProjectClip; focused: boolean; onOpen: () => void; onSelect: (selected: boolean) => void; onDelete: () => void }) {
  return (
    <article className={focused ? "scan-row scan-row--focused" : "scan-row"} onClick={onOpen}>
      <label className="scan-row__check" onClick={(event) => event.stopPropagation()}><input type="checkbox" checked={clip.selected} onChange={(event) => onSelect(event.target.checked)} /></label>
      <div className="scan-row__main"><strong>{clip.title}</strong><span className="scan-row__meta">{clock(clip.start_seconds)}–{clock(clip.end_seconds)} · {clip.duration_seconds.toFixed(1)}s</span></div>
      <Badge tone={clip.source === "manual" ? "neutral" : "success"}>{clip.source}</Badge>
      <div className="scan-row__actions"><Button onClick={(event) => { event.stopPropagation(); onOpen(); }}>Open</Button><Button variant="danger" onClick={(event) => { event.stopPropagation(); onDelete(); }}>Delete</Button></div>
    </article>
  );
}

function SmartEditCard({ sequence, story, onOpen }: { sequence: EditSequence; story?: StoryConcept; onOpen: () => void }) {
  const tone = sequence.integrity.status === "failed" ? "danger" : sequence.integrity.status === "review_required" ? "warning" : "success";
  return (
    <article className="story-card" onClick={onOpen} role="button" tabIndex={0} onKeyDown={(event) => { if (event.key === "Enter") onOpen(); }}>
      <div className="story-card__top"><strong>{sequence.title}</strong><Badge tone={tone}>{sequence.integrity.status.replace(/_/g, " ")}</Badge></div>
      {story?.hook ? <p className="story-card__hook">“{story.hook}”</p> : <p className="muted">{story?.explanation ?? "Source-grounded multi-segment edit"}</p>}
      <div className="story-card__meta"><span>{sequence.duration_seconds.toFixed(1)}s</span><span>{sequence.segment_count} segments</span></div>
    </article>
  );
}

function SmartEditEditor({ sequence, sourcePath, onClose, onCommand, onStartJob, onRelinkEnhancementAsset }: { sequence: EditSequence; sourcePath: string; onClose: () => void; onCommand: CommandHandler; onStartJob: JobHandler; onRelinkEnhancementAsset: (editSequenceId: string, assetId: string) => Promise<void> }) {
  const [title, setTitle] = useState(sequence.title);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [section, setSection] = useState<"story" | "visual" | "enhancements">("story");
  const [framing, setFraming] = useState(sequence.visual_plan?.framing_mode ?? "auto");
  const [emphasis, setEmphasis] = useState(sequence.visual_plan?.visual_emphasis ?? "automatic");
  const [captionPreset, setCaptionPreset] = useState(sequence.visual_plan?.caption_preset ?? "word_highlight");
  const [captionPosition, setCaptionPosition] = useState(sequence.visual_plan?.caption_position ?? "auto");
  const ids = sequence.segments.map((item) => item.id);
  const tone = sequence.integrity.status === "failed" ? "danger" : sequence.integrity.status === "review_required" ? "warning" : "success";
  const renderedPath = sequence.preview_path ?? sequence.render_path;

  return <Panel title="Smart Edit review" action={<Button variant="quiet" onClick={onClose}>Close</Button>}>
    {renderedPath ? <VideoPreview path={renderedPath} vertical /> : <VideoPreview path={sourcePath} start={sequence.segments[0]?.source_in} end={sequence.segments[0]?.source_out} />}

    <div className="story-header">
      <label className="field"><span className="field__label">Name</span><Input value={title} onChange={(event) => setTitle(event.target.value)} /></label>
      <Button onClick={() => onCommand("update_edit_sequence", { edit_sequence_id: sequence.id, operation: "rename", title })}>Save name</Button>
    </div>

    <div className="story-header__integrity"><Badge tone={tone}>{sequence.integrity.status.replace(/_/g, " ")}</Badge>{sequence.integrity.warnings.length ? <span className="muted">{sequence.integrity.warnings.join(" ")}</span> : null}</div>

    <SegmentedControl ariaLabel="Smart Edit inspector" value={section} onChange={setSection} options={[{ value: "story", label: "Story" }, { value: "visual", label: "Visual" }, { value: "enhancements", label: "Enhancements" }]} />

    {section === "story" ? <div className="story-structure">
      {sequence.segments.map((segment, index) => (
        <article className="story-beat" key={segment.id}>
          <div className="story-beat__top"><span className="story-beat__role">{purposeLabel(segment.purpose)}</span><span className="story-beat__time">{clock(segment.source_in)}–{clock(segment.source_out)} · {segment.duration_seconds.toFixed(1)}s</span></div>
          <p className="story-beat__quote">“{segment.transcript_excerpt}”</p>
          {segment.actions.length ? <div className="story-beat__actions">{segment.actions.map((action) => <ActionChip key={action.id} label={actionLabel(action.type)} active={action.enabled} onToggle={() => onCommand("update_edit_sequence", { edit_sequence_id: sequence.id, operation: "toggle_action", action_id: action.id, enabled: !action.enabled })} />)}</div> : null}
          <div className="story-beat__controls">
            <Button variant="quiet" disabled={index === 0} onClick={() => { const next = [...ids]; [next[index - 1], next[index]] = [next[index], next[index - 1]]; void onCommand("update_edit_sequence", { edit_sequence_id: sequence.id, operation: "reorder", segment_ids: next }); }}>Move up</Button>
            <Button variant="quiet" onClick={() => setExpandedId(expandedId === segment.id ? null : segment.id)}>{expandedId === segment.id ? "Hide timing" : "Adjust timing"}</Button>
            <Button variant="danger" onClick={() => onCommand("update_edit_sequence", { edit_sequence_id: sequence.id, operation: "remove_segment", segment_id: segment.id })}>Remove</Button>
          </div>
          {expandedId === segment.id ? <SegmentTrim sequenceId={sequence.id} segment={segment} onCommand={onCommand} /> : null}
        </article>
      ))}
    </div> : null}

    {section === "visual" ? <div className="inspector-section">
      {sequence.visual_plan ? <>
        <div className="timing-grid">
          <div className="field"><span className="field__label">Framing</span><SegmentedControl ariaLabel="Smart Edit framing" value={framing} onChange={setFraming} options={[{ value: "auto", label: "Auto" }, { value: "fixed", label: "Fixed" }]} /></div>
          <div className="field"><span className="field__label">Visual emphasis</span><SegmentedControl ariaLabel="Visual emphasis" value={emphasis} onChange={setEmphasis} options={[{ value: "automatic", label: "Automatic" }, { value: "off", label: "Off" }]} /></div>
          <label className="field"><span className="field__label">Captions</span><Select value={captionPreset} onChange={(event) => setCaptionPreset(event.target.value as typeof captionPreset)}><option value="clean">Clean</option><option value="bold_social">Bold</option><option value="word_highlight">Word Highlight</option></Select></label>
          <label className="field"><span className="field__label">Caption position</span><Select value={captionPosition} onChange={(event) => setCaptionPosition(event.target.value as typeof captionPosition)}><option value="auto">Auto safe zone</option><option value="upper">Upper</option><option value="center">Center</option><option value="lower">Lower</option></Select></label>
        </div>
        <div className="editor-actions"><Button variant="primary" onClick={() => onCommand("update_visual_plan", { edit_sequence_id: sequence.id, operation: "settings", framing_mode: framing, visual_emphasis: emphasis, caption_preset: captionPreset, caption_position: captionPosition })}>Save visual treatment</Button><Button variant="quiet" onClick={() => onStartJob("visual_analyze", { edit_sequence_id: sequence.id })}>Refresh visual analysis</Button></div>
        {sequence.visual_plan.warnings.length ? <div className="notice notice--warning">{sequence.visual_plan.warnings.map((warning) => <span key={warning}>{warning}</span>)}</div> : null}
        <div className="action-list"><p className="field__label">Automatic visual actions</p>{sequence.visual_plan.actions.length ? sequence.visual_plan.actions.map((action) => <ActionChip key={action.id} label={`${actionLabel(action.type)} · ${clock(action.timeline_start)}–${clock(action.timeline_end)}`} active={action.enabled} onToggle={() => onCommand("update_visual_plan", { edit_sequence_id: sequence.id, operation: "toggle_action", action_id: action.id, enabled: !action.enabled })} />) : <p className="muted">No automatic visual actions were needed.</p>}</div>
      </> : <EmptyState title="Visual treatment not prepared" description="Analyze the approved source ranges to create stable framing, safe captions, and restrained emphasis." action={<Button variant="primary" onClick={() => onStartJob("visual_analyze", { edit_sequence_id: sequence.id })}>Analyze visuals</Button>} />}
    </div> : null}

    {section === "enhancements" ? <div className="inspector-section">
      {sequence.enhancement_plan ? <>
        <p className="muted">Optional additions remain separate from the story edit. Turn off anything that does not help.</p>
        {[...sequence.enhancement_plan.broll_items.map((item) => ({ ...item, kind: "B-roll", label: item.reason ?? "Supporting visual" })), ...sequence.enhancement_plan.graphic_items.map((item) => ({ ...item, kind: "Graphic", label: item.text ?? "Source-grounded graphic" })), ...sequence.enhancement_plan.sound_cues.map((item) => ({ ...item, kind: "Sound", label: item.purpose ?? "Sound accent" }))].map((item) => <article className="enhancement-row" key={item.id}><label><input type="checkbox" checked={item.enabled} onChange={(event) => onCommand("update_enhancement_plan", { edit_sequence_id: sequence.id, operation: "toggle", item_id: item.id, enabled: event.target.checked })} /><span><strong>{item.kind}</strong><small>{item.label} · {clock(item.timeline_start)}{item.timeline_end !== undefined ? `–${clock(item.timeline_end)}` : ""}</small></span></label>{item.asset_id ? <Button variant="quiet" onClick={() => onRelinkEnhancementAsset(sequence.id, item.asset_id!)}>Locate asset</Button> : null}</article>)}
        {sequence.enhancement_plan.music_track ? <article className="enhancement-row"><label><input type="checkbox" checked={sequence.enhancement_plan.music_track.enabled} onChange={(event) => onCommand("update_enhancement_plan", { edit_sequence_id: sequence.id, operation: "toggle", item_id: "music", enabled: event.target.checked })} /><span><strong>Music</strong><small>{sequence.enhancement_plan.music_track.reason}</small></span></label><Button variant="quiet" onClick={() => onRelinkEnhancementAsset(sequence.id, sequence.enhancement_plan!.music_track!.asset_id)}>Locate asset</Button></article> : <p className="muted">No music bed selected.</p>}
        {sequence.enhancement_plan.warnings.length ? <div className="notice notice--warning"><strong>Some assets need attention</strong>{sequence.enhancement_plan.warnings.map((warning) => <span key={warning}>{warning}</span>)}</div> : null}
        <Button variant="quiet" onClick={() => onStartJob("enhancement_plan", { edit_sequence_id: sequence.id })}>Refresh enhancement plan</Button>
      </> : <EmptyState title="No enhancement plan" description="A clean edit is valid. Plan restrained graphics or local supporting assets only when they add clarity." action={<Button disabled={!sequence.visual_plan} onClick={() => onStartJob("enhancement_plan", { edit_sequence_id: sequence.id })}>Plan enhancements</Button>} />}
    </div> : null}

    <div className="editor-actions">
      <Button disabled={sequence.integrity.status === "failed" || sequence.segment_count < 2} onClick={() => onStartJob("smart_preview", { edit_sequence_id: sequence.id })}>Generate preview</Button>
      <Button disabled={sequence.integrity.status === "failed" || sequence.segment_count < 2} onClick={() => onStartJob("smart_render", { edit_sequence_id: sequence.id })}>Render MP4</Button>
      <Button onClick={() => onStartJob("export", { format: "otio", edit_sequence_id: sequence.id })}>Export OTIO</Button>
      <Button onClick={() => onStartJob("export", { format: "premiere_xml", edit_sequence_id: sequence.id })}>Export XML</Button>
    </div>
  </Panel>;
}

function SegmentTrim({ sequenceId, segment, onCommand }: { sequenceId: string; segment: EditSegment; onCommand: CommandHandler }) {
  const [sourceIn, setSourceIn] = useState(String(segment.source_in));
  const [sourceOut, setSourceOut] = useState(String(segment.source_out));
  return (
    <div className="timing-grid">
      <label className="field"><span className="field__label">Source in</span><Input type="number" step="0.01" value={sourceIn} onChange={(event) => setSourceIn(event.target.value)} /></label>
      <label className="field"><span className="field__label">Source out</span><Input type="number" step="0.01" value={sourceOut} onChange={(event) => setSourceOut(event.target.value)} /></label>
      <Button disabled={Number(sourceOut) <= Number(sourceIn)} onClick={() => onCommand("update_edit_sequence", { edit_sequence_id: sequenceId, operation: "trim", segment_id: segment.id, source_in: Number(sourceIn), source_out: Number(sourceOut) })}>Save trim</Button>
    </div>
  );
}

function ClipEditor({ clip, sourcePath, onClose, onCommand, onStartJob }: { clip: ProjectClip; sourcePath: string; onClose: () => void; onCommand: CommandHandler; onStartJob: JobHandler }) {
  const [title, setTitle] = useState(clip.title);
  const [start, setStart] = useState(String(clip.start_seconds));
  const [end, setEnd] = useState(String(clip.end_seconds));
  const [framing, setFraming] = useState(clip.framing_mode);
  const [cropX, setCropX] = useState(clip.manual_crop.crop_x);
  const [cropY, setCropY] = useState(clip.manual_crop.crop_y);
  const [zoom, setZoom] = useState(clip.manual_crop.scale);
  const [captions, setCaptions] = useState(clip.captions_enabled);
  const [preset, setPreset] = useState(clip.caption_preset);
  const duration = Math.max(0, Number(end) - Number(start));

  return <Panel title="Clip editor" action={<Button variant="quiet" onClick={onClose}>Close</Button>}>
    <div className="clip-stage">
      <VideoPreview path={clip.preview_path ?? sourcePath} start={clip.preview_path ? 0 : clip.start_seconds} end={clip.preview_path ? undefined : clip.end_seconds} vertical={Boolean(clip.preview_path)} />
      {clip.preview_path && captions ? <span className={`caption-overlay caption-overlay--${preset}`}>Sample caption text</span> : null}
    </div>
    {clip.preview_path && captions ? <p className="muted">Preview shows the caption style. Exact timing appears once rendered.</p> : null}

    <div className="clip-editor-form">
      <label className="field"><span className="field__label">Title</span><Input value={title} onChange={(event) => setTitle(event.target.value)} /></label>
      <div className="timing-grid"><label className="field"><span className="field__label">Start, seconds</span><Input type="number" min="0" step="0.01" value={start} onChange={(event) => setStart(event.target.value)} /></label><label className="field"><span className="field__label">End, seconds</span><Input type="number" min="0" step="0.01" value={end} onChange={(event) => setEnd(event.target.value)} /></label><div><span className="field__label">Duration</span><strong>{duration.toFixed(2)}s</strong></div></div>

      <div className="field"><span className="field__label">Framing</span><SegmentedControl ariaLabel="Framing" value={framing} onChange={setFraming} options={[{ value: "auto", label: "Auto" }, { value: "manual", label: "Fixed" }]} /></div>
      {framing === "manual" ? <div className="range-grid"><label>Horizontal <input type="range" min="0" max="1" step="0.01" value={cropX} onChange={(event) => setCropX(Number(event.target.value))} /></label><label>Vertical <input type="range" min="0" max="1" step="0.01" value={cropY} onChange={(event) => setCropY(Number(event.target.value))} /></label><label>Zoom <input type="range" min="1" max="3" step="0.05" value={zoom} onChange={(event) => setZoom(Number(event.target.value))} /></label></div> : null}

      <label className="check-row"><input type="checkbox" checked={captions} onChange={(event) => setCaptions(event.target.checked)} /> Captions enabled</label>
      {captions ? <div className="field"><span className="field__label">Caption preset</span><SegmentedControl ariaLabel="Caption preset" value={preset} onChange={setPreset} options={CAPTION_PRESETS} /></div> : null}

      <Button variant="primary" disabled={duration <= 0} onClick={() => onCommand("update_clip", { clip_id: clip.id, title, start_seconds: Number(start), end_seconds: Number(end), framing_mode: framing, manual_crop: { crop_x: cropX, crop_y: cropY, scale: zoom }, captions_enabled: captions, caption_preset: preset })}>Save clip</Button>
      <div className="editor-actions"><Button onClick={() => onStartJob("reframe", { clip_id: clip.id })}>Analyze framing</Button><Button disabled={!clip.has_reframe} onClick={() => onStartJob("preview", { clip_id: clip.id })}>Generate 9:16 preview</Button><Button disabled={!clip.has_reframe} onClick={() => onStartJob("render", { clip_id: clip.id })}>Render MP4</Button></div>
      <p className="muted">{clip.has_reframe ? "Framing data ready" : "Analyze framing before preview or render"} · {clip.caption_cue_count} caption cues</p>
    </div>
  </Panel>;
}
