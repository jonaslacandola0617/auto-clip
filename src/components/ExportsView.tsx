import type { EditSequence, OutputArtifact, ProductionRun, ProjectClip } from "../types";
import { Button, EmptyState, Panel } from "./Ui";

type JobHandler = (type: string, payload?: Record<string, unknown>) => Promise<void>;

export function ExportsView({ clips, editSequences, productionRuns, outputs, busy, onStartJob }: { clips: ProjectClip[]; editSequences: EditSequence[]; productionRuns: ProductionRun[]; outputs: OutputArtifact[]; busy: boolean; onStartJob: JobHandler }) {
  const chosen = clips.find((clip) => clip.selected) ?? clips[0];
  const smartEdit = editSequences.find((item) => item.status === "ready" && item.editorial_review?.accepted);
  const productionRun = productionRuns.at(-1);

  return <div className="page">
    <header className="page-header"><div><p className="eyebrow">Exports</p><h1>Get your finished video out</h1></div></header>

    <div className="exports-layout">
      <Panel title="Finished video" className="export-primary">
        <p>Render an approved Highlight Clip or Smart Edit as an H.264/AAC vertical MP4.</p>
        <div className="button-row"><Button variant="primary" disabled={!chosen || busy} onClick={() => chosen && onStartJob("render", { clip_id: chosen.id })}>Render Highlight Clip</Button><Button variant="primary" disabled={!smartEdit || busy} onClick={() => smartEdit && onStartJob("smart_render", { edit_sequence_id: smartEdit.id })}>Render Smart Edit</Button></div>
        {chosen || smartEdit ? <p className="muted">{chosen ? `Highlight: ${chosen.title}` : ""}{chosen && smartEdit ? " · " : ""}{smartEdit ? `Smart Edit: ${smartEdit.title}` : ""}</p> : <p className="muted">Approve a clip or Smart Edit to enable rendering.</p>}
      </Panel>

      <div className="export-secondary-grid">
        <Panel title="Captions"><p>Export structured captions for the selected clip.</p><div className="button-row"><Button disabled={!chosen || !chosen.captions_enabled || busy} onClick={() => chosen && onStartJob("export", { format: "srt", clip_id: chosen.id })}>Export SRT</Button><Button disabled={!chosen || !chosen.captions_enabled || busy} onClick={() => chosen && onStartJob("export", { format: "ass", clip_id: chosen.id })}>Export ASS</Button></div></Panel>
        <Panel title="Continue in an editor"><p>Reframing, captions, and enhancements may not transfer faithfully; exports include explicit warnings.</p><div className="editable-export-group"><strong>Highlight Clip</strong><div className="button-row"><Button disabled={!chosen || busy} onClick={() => chosen && onStartJob("export", { format: "otio", clip_id: chosen.id })}>OTIO</Button><Button disabled={!chosen || busy} onClick={() => chosen && onStartJob("export", { format: "premiere_xml", clip_id: chosen.id })}>Premiere XML</Button></div></div><div className="editable-export-group"><strong>Smart Edit</strong><div className="button-row"><Button disabled={!smartEdit || busy} onClick={() => smartEdit && onStartJob("export", { format: "otio", edit_sequence_id: smartEdit.id })}>OTIO</Button><Button disabled={!smartEdit || busy} onClick={() => smartEdit && onStartJob("export", { format: "premiere_xml", edit_sequence_id: smartEdit.id })}>Premiere XML</Button></div></div></Panel>
        <Panel title="Production package"><p>Package approved batch outputs with videos, captions, editable timelines, and a manifest.</p><Button disabled={!productionRun?.generated_edit_ids.length || busy} onClick={() => productionRun && onStartJob("production_package", { production_run_id: productionRun.id })}>Create production package</Button>{productionRun?.output_package_path ? <p className="muted">Ready: {productionRun.output_package_path}</p> : null}</Panel>
        <Panel title="Project data"><p>Export a portable snapshot of the canonical AutoClip project.</p><Button disabled={busy} onClick={() => onStartJob("export", { format: "project_json" })}>Export project JSON</Button></Panel>
      </div>

      <Panel title="Generated outputs" className="outputs-panel">
        {outputs.length ? <div className="output-list">{[...outputs].reverse().map((output) => (
          <article key={output.id} className="output-row">
            <div><strong>{output.kind.replace(/_/g, " ")}</strong><span>{output.path}</span></div>
            {output.warnings.length ? <p className="output-row__warning">{output.warnings.join(" ")}</p> : null}
          </article>
        ))}</div> : <EmptyState title="No exports yet" description="Finished videos, captions, timelines, and project snapshots will appear here." />}
      </Panel>
    </div>
  </div>;
}
