import { useState } from "react";
import type { Doctor, Job, ProjectState } from "../types";
import { Button, EmptyState, Panel, Progress, Status } from "./Ui";

type Tab = "overview" | "transcript" | "clips" | "exports";

function formatDuration(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${Math.floor(seconds % 60).toString().padStart(2, "0")}`;
}

export function WorkspaceView({ project, doctor, jobs, onChooseSource, onStartJob, onCancelJob }: { project: ProjectState; doctor: Doctor | null; jobs: Job[]; onChooseSource: () => void; onStartJob: (type: string) => void; onCancelJob: (id: string) => void }) {
  const [tab, setTab] = useState<Tab>("overview");
  const activeJob = jobs.find((job) => job.state === "queued" || job.state === "running");
  const tabs: Array<{ id: Tab; label: string }> = [{ id: "overview", label: "Overview" }, { id: "transcript", label: "Transcript" }, { id: "clips", label: "Clips" }, { id: "exports", label: "Exports" }];

  let primaryAction = <Button variant="primary" onClick={onChooseSource}>{project.status === "source_missing" ? "Locate file" : "Select video"}</Button>;
  if (project.status === "needs_transcript") primaryAction = <Button variant="primary" disabled={!doctor?.whisper.ready || Boolean(activeJob)} onClick={() => onStartJob("transcribe")}>{activeJob ? "Processing…" : "Transcribe"}</Button>;
  if (project.status === "needs_analysis") primaryAction = <Button variant="primary" disabled title={!doctor?.gemini.ready ? "Configure Gemini in Settings" : "Analysis arrives in Phase 1B"}>Analyze</Button>;
  if (project.status === "ready") primaryAction = <Button variant="primary" disabled>Review clips</Button>;

  return (
    <div className="page">
      <header className="workspace-header"><div><p className="eyebrow">Project workspace</p><h1>{project.name}</h1><p>{project.source?.filename ?? "No source selected"}</p></div><div className="workspace-header__actions"><Status ready={project.status !== "source_missing"}>{project.status === "source_missing" ? "Source unavailable" : "Autosaved"}</Status>{primaryAction}</div></header>
      <nav className="tabs" aria-label="Project views">{tabs.map((item) => <button key={item.id} className={tab === item.id ? "tab tab--active" : "tab"} onClick={() => setTab(item.id)}>{item.label}</button>)}</nav>

      {tab === "overview" ? <div className="workspace-grid">
        <Panel title="Source media" className="source-panel">
          {project.source ? <div className="source-card"><div className="source-card__preview"><span aria-hidden="true">▶</span></div><div className="source-card__details"><div><span>File</span><strong>{project.source.filename}</strong></div><div><span>Duration</span><strong>{formatDuration(project.source.duration_seconds)}</strong></div><div><span>Frame</span><strong>{project.source.width} × {project.source.height}</strong></div><div><span>Rate</span><strong>{project.source.frame_rate.toFixed(2)} fps</strong></div><div><span>Video</span><strong>{project.source.codec}</strong></div><div><span>Audio</span><strong>{project.source.has_audio ? "Present" : "Not detected"}</strong></div></div>{!project.source.available ? <div className="notice notice--warning"><strong>Source media unavailable</strong><span>Locate the original file to continue. AutoClip will not silently replace it.</span><Button onClick={onChooseSource}>Locate file</Button></div> : null}</div> : <EmptyState title="Select a source video" description="Choose local footage to inspect its timing, streams, and processing options." action={<Button variant="primary" onClick={onChooseSource}>Select video</Button>} />}
        </Panel>
        <Panel title="Processing"><div className="action-list"><div><span className="action-list__index">01</span><div><strong>Transcription</strong><p>{project.transcript_count ? "Transcript ready" : "Create a local word-timed transcript."}</p></div><Status ready={project.transcript_count > 0}>{project.transcript_count ? "Ready" : "Waiting"}</Status></div><div><span className="action-list__index">02</span><div><strong>Clip analysis</strong><p>{doctor?.gemini.ready ? "Gemini is configured for a later analysis step." : "Configure Gemini before AI analysis."}</p></div><Status ready={Boolean(doctor?.gemini.ready)}>{doctor?.gemini.ready ? "Configured" : "Not configured"}</Status></div></div></Panel>
        <Panel title="Current and recent jobs" className="jobs-panel"><JobList jobs={jobs} onCancel={onCancelJob} /></Panel>
      </div> : null}

      {tab === "transcript" ? (project.transcript_count ? <Panel title="Transcript"><p className="muted">Transcript data is available. The advanced transcript editor belongs to a later phase.</p></Panel> : <EmptyState title="No transcript yet" description="Transcribe the source video to begin finding clips." action={project.source?.available ? <Button variant="primary" disabled={!doctor?.whisper.ready || Boolean(activeJob)} onClick={() => onStartJob("transcribe")}>Transcribe</Button> : undefined} />) : null}
      {tab === "clips" ? <EmptyState title="No clips yet" description={project.transcript_count ? "AI clip analysis arrives in the next authorized phase." : "Analyze the transcript after transcription completes."} /> : null}
      {tab === "exports" ? <EmptyState title="No exports yet" description="Approved timelines and finished videos will appear here." /> : null}
    </div>
  );
}

function JobList({ jobs, onCancel }: { jobs: Job[]; onCancel: (id: string) => void }) {
  if (!jobs.length) return <EmptyState title="No processing jobs" description="Jobs will appear here with progress, recovery guidance, and results." />;
  return <div className="job-list">{jobs.slice(0, 6).map((job) => <article className="job-row" key={job.id}><div className="job-row__top"><div><strong>{job.type === "transcribe" ? "Transcribe source" : "System check"}</strong><span>{job.current_stage}</span></div><Status ready={job.state === "completed"}>{job.state}</Status></div>{job.state === "running" || job.state === "queued" ? <Progress value={job.progress} /> : null}{job.error ? <p className="error-copy">{job.error.message}</p> : null}{job.cancellable && (job.state === "running" || job.state === "queued") ? <Button variant="quiet" onClick={() => onCancel(job.id)}>Cancel safely</Button> : null}</article>)}</div>;
}

