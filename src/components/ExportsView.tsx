import type { OutputArtifact, ProjectClip } from "../types";
import { Button, EmptyState, Panel } from "./Ui";

type JobHandler = (type: string, payload?: Record<string, unknown>) => Promise<void>;

export function ExportsView({ clips, outputs, busy, onStartJob }: { clips: ProjectClip[]; outputs: OutputArtifact[]; busy: boolean; onStartJob: JobHandler }) {
  const chosen = clips.find((clip) => clip.selected) ?? clips[0];
  return <div className="exports-grid">
    <Panel title="Finished video"><p>Render selected clips as H.264/AAC vertical MP4 from the Clips editor.</p><Button disabled={!chosen || busy} onClick={() => chosen && onStartJob("render", { clip_id: chosen.id })}>Render selected MP4</Button></Panel>
    <Panel title="Captions"><p>Export structured clip captions through delivery adapters.</p><div className="button-row"><Button disabled={!chosen || !chosen.captions_enabled || busy} onClick={() => chosen && onStartJob("export", { format: "srt", clip_id: chosen.id })}>Export SRT</Button><Button disabled={!chosen || !chosen.captions_enabled || busy} onClick={() => chosen && onStartJob("export", { format: "ass", clip_id: chosen.id })}>Export ASS</Button></div></Panel>
    <Panel title="Editable timeline"><p>Reframing and caption styling may not transfer faithfully. AutoClip includes warnings with editable timeline exports.</p><div className="button-row"><Button disabled={!clips.length || busy} onClick={() => onStartJob("export", { format: "otio" })}>DaVinci Resolve OTIO</Button><Button disabled={!clips.length || busy} onClick={() => onStartJob("export", { format: "premiere_xml" })}>Premiere XML</Button></div></Panel>
    <Panel title="Project"><p>Export a portable snapshot of the canonical AutoClip JSON.</p><Button disabled={busy} onClick={() => onStartJob("export", { format: "project_json" })}>Export project JSON</Button></Panel>
    <Panel title="Generated outputs" className="outputs-panel">{outputs.length ? <div className="output-list">{[...outputs].reverse().map((output) => <article key={output.id}><div><strong>{output.kind.replace("_", " ").toUpperCase()}</strong><span>{output.path}</span></div>{output.warnings.length ? <p>{output.warnings.join(" ")}</p> : null}</article>)}</div> : <EmptyState title="No exports yet" description="Finished videos, captions, timelines, and project snapshots will appear here." />}</Panel>
  </div>;
}
