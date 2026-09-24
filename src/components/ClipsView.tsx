import { useState } from "react";
import type { ClipCandidate, ProjectClip } from "../types";
import { Button, EmptyState, Input, Panel, Select, Status } from "./Ui";
import { VideoPreview } from "./VideoPreview";

type CommandHandler = (command: string, payload: Record<string, unknown>) => Promise<void>;
type JobHandler = (type: string, payload?: Record<string, unknown>) => Promise<void>;

function clock(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${Math.floor(seconds % 60).toString().padStart(2, "0")}`;
}

export function ClipsView({ candidates, clips, sourcePath, geminiReady, busy, onCommand, onStartJob }: {
  candidates: ClipCandidate[];
  clips: ProjectClip[];
  sourcePath: string;
  geminiReady: boolean;
  busy: boolean;
  onCommand: CommandHandler;
  onStartJob: JobHandler;
}) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const editing = clips.find((clip) => clip.id === editingId) ?? null;
  return <div className="clips-layout">
    <div>
      <Panel title="AI candidates" action={<Button variant="primary" disabled={!geminiReady || busy} onClick={() => onStartJob("analyze")}>{busy ? "Analyzing…" : "Analyze clips"}</Button>}>
        {!geminiReady ? <div className="notice notice--warning"><span>Gemini isn't configured yet. Manual clip creation remains available in Transcript.</span></div> : null}
        {candidates.length ? <div className="candidate-list">{candidates.map((candidate) => <CandidateCard key={candidate.id} candidate={candidate} onCreate={() => onCommand("create_clip_from_candidate", { candidate_id: candidate.id })} />)}</div> : <EmptyState title="No AI candidates yet" description="Analyze the transcript with Gemini, or create clips manually from transcript segments." />}
      </Panel>
      <Panel title="Project clips">
        {clips.length ? <div className="clip-list">{clips.map((clip) => <article className="clip-card" key={clip.id}>
          <div className="clip-card__title"><div><strong>{clip.title}</strong><span>{clock(clip.start_seconds)}–{clock(clip.end_seconds)} · {clip.duration_seconds.toFixed(1)}s</span></div><Status ready={clip.source === "manual"}>{clip.source}</Status></div>
          <div className="clip-card__actions"><label><input type="checkbox" checked={clip.selected} onChange={(event) => onCommand("select_clip", { clip_id: clip.id, selected: event.target.checked })} /> Select</label><Button onClick={() => setEditingId(clip.id)}>Preview</Button><Button onClick={() => setEditingId(clip.id)}>Edit</Button><Button variant="quiet" onClick={() => setEditingId(clip.id)}>Rename</Button><Button variant="danger" onClick={() => onCommand("delete_clip", { clip_id: clip.id })}>Delete</Button></div>
        </article>)}</div> : <EmptyState title="No clips yet" description="Create a manual clip in Transcript or accept an AI candidate." />}
      </Panel>
    </div>
    {editing ? <ClipEditor key={`${editing.id}-${editing.revision}`} clip={editing} sourcePath={sourcePath} onClose={() => setEditingId(null)} onCommand={onCommand} onStartJob={onStartJob} /> : <Panel title="Clip editor"><EmptyState title="Choose a clip" description="Preview, trim, frame, caption, and render a project clip." /></Panel>}
  </div>;
}

function CandidateCard({ candidate, onCreate }: { candidate: ClipCandidate; onCreate: () => void }) {
  return <article className="candidate-card"><div><strong>{candidate.title}</strong><span>{clock(candidate.start_seconds)}–{clock(candidate.end_seconds)} · {candidate.duration_seconds.toFixed(1)}s</span></div><p>{candidate.reason}</p><div className="candidate-card__meta"><span>{candidate.category}</span><span>Score {candidate.score}</span><Button onClick={onCreate}>Create clip</Button></div></article>;
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
    <VideoPreview path={clip.preview_path ?? sourcePath} start={clip.preview_path ? 0 : clip.start_seconds} end={clip.preview_path ? undefined : clip.end_seconds} vertical={Boolean(clip.preview_path)} />
    <div className="clip-editor-form">
      <label className="field"><span className="field__label">Title</span><Input value={title} onChange={(event) => setTitle(event.target.value)} /></label>
      <div className="timing-grid"><label className="field"><span className="field__label">Start, seconds</span><Input type="number" min="0" step="0.01" value={start} onChange={(event) => setStart(event.target.value)} /></label><label className="field"><span className="field__label">End, seconds</span><Input type="number" min="0" step="0.01" value={end} onChange={(event) => setEnd(event.target.value)} /></label><div><span className="field__label">Duration</span><strong>{duration.toFixed(2)}s</strong></div></div>
      <label className="field"><span className="field__label">Framing</span><Select value={framing} onChange={(event) => setFraming(event.target.value as ProjectClip["framing_mode"])}><option value="auto">Auto</option><option value="manual">Manual</option></Select></label>
      {framing === "manual" ? <div className="range-grid"><label>Horizontal <input type="range" min="0" max="1" step="0.01" value={cropX} onChange={(event) => setCropX(Number(event.target.value))} /></label><label>Vertical <input type="range" min="0" max="1" step="0.01" value={cropY} onChange={(event) => setCropY(Number(event.target.value))} /></label><label>Zoom <input type="range" min="1" max="3" step="0.05" value={zoom} onChange={(event) => setZoom(Number(event.target.value))} /></label></div> : null}
      <label className="check-row"><input type="checkbox" checked={captions} onChange={(event) => setCaptions(event.target.checked)} /> Captions enabled</label>
      <label className="field"><span className="field__label">Caption preset</span><Select disabled={!captions} value={preset} onChange={(event) => setPreset(event.target.value as ProjectClip["caption_preset"])}><option value="clean">Clean</option><option value="bold_social">Bold Social</option><option value="word_highlight">Word Highlight</option></Select></label>
      <Button variant="primary" disabled={duration <= 0} onClick={() => onCommand("update_clip", { clip_id: clip.id, title, start_seconds: Number(start), end_seconds: Number(end), framing_mode: framing, manual_crop: { crop_x: cropX, crop_y: cropY, scale: zoom }, captions_enabled: captions, caption_preset: preset })}>Save clip</Button>
      <div className="editor-actions"><Button onClick={() => onStartJob("reframe", { clip_id: clip.id })}>Analyze framing</Button><Button disabled={!clip.has_reframe} onClick={() => onStartJob("preview", { clip_id: clip.id })}>Generate 9:16 preview</Button><Button disabled={!clip.has_reframe} onClick={() => onStartJob("render", { clip_id: clip.id })}>Render MP4</Button></div>
      <p className="muted">{clip.has_reframe ? "Framing data ready" : "Analyze framing before preview or render"} · {clip.caption_cue_count} caption cues</p>
    </div>
  </Panel>;
}
