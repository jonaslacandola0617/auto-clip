import type { Doctor, Job, ProjectState } from "../types";
import { Button, EmptyState, Panel, Progress, Status, WorkflowSteps } from "./Ui";
import { VideoPreview } from "./VideoPreview";
import type { View } from "./AppShell";

function formatDuration(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${Math.floor(seconds % 60).toString().padStart(2, "0")}`;
}

export function OverviewView({ project, doctor, jobs, onChooseSource, onStartJob, onCancelJob, onNavigate }: {
  project: ProjectState;
  doctor: Doctor | null;
  jobs: Job[];
  onChooseSource: () => void;
  onStartJob: (type: string, payload?: Record<string, unknown>) => Promise<void>;
  onCancelJob: (id: string) => void;
  onNavigate: (view: View) => void;
}) {
  const activeJob = jobs.find((job) => job.state === "queued" || job.state === "running");
  const hasAnalysis = project.moment_count > 0 || project.candidate_count > 0 || project.story_concepts.length > 0;
  const hasClips = project.clips.length > 0 || project.edit_sequences.length > 0;
  const hasVisualEdit = project.clips.some((clip) => clip.has_reframe) || project.edit_sequences.some((sequence) => Boolean(sequence.visual_plan || sequence.preview_path));
  const hasExport = project.outputs.length > 0 || project.production_runs.some((run) => Boolean(run.output_package_path));

  const steps: Array<{ label: string; state: "done" | "current" | "upcoming" }> = [
    { label: "Source", state: project.source?.available ? "done" : "current" },
    { label: "Transcript", state: project.transcript_count > 0 ? "done" : project.source?.available ? "current" : "upcoming" },
    { label: "Analysis", state: hasAnalysis ? "done" : project.transcript_count > 0 ? "current" : "upcoming" },
    { label: "Clips", state: hasClips ? "done" : hasAnalysis ? "current" : "upcoming" },
    { label: "Visual edit", state: hasVisualEdit ? "done" : hasClips ? "current" : "upcoming" },
    { label: "Export", state: hasExport ? "done" : hasVisualEdit ? "current" : "upcoming" },
  ];

  let title = "Select a source video";
  let description = "Choose local footage to begin. AutoClip keeps the original file untouched.";
  let cta = <Button variant="primary" onClick={onChooseSource}>{project.status === "source_missing" ? "Locate file" : "Select video"}</Button>;

  if (project.status === "needs_transcript") {
    title = "Transcribe the source";
    description = "AutoClip creates a local, word-timed transcript before it can find anything worth clipping.";
    cta = <Button variant="primary" disabled={!doctor?.whisper.ready || Boolean(activeJob)} onClick={() => onStartJob("transcribe")}>{activeJob ? "Transcribing…" : "Transcribe"}</Button>;
  } else if (project.status === "needs_analysis") {
    title = "Find what's worth clipping";
    description = "AutoClip scans the transcript for standout moments and multi-part stories.";
    cta = <Button variant="primary" disabled={!doctor?.gemini.ready || Boolean(activeJob)} title={!doctor?.gemini.ready ? "Configure Gemini in Settings" : undefined} onClick={async () => { await onStartJob("analyze"); onNavigate("clips"); }}>{activeJob ? "Analyzing…" : "Analyze video"}</Button>;
  } else if (project.status === "ready") {
    title = hasClips ? "Review what AutoClip found" : "Nothing selected yet";
    description = hasClips ? "Accept, trim, and approve clips and Smart Edits before exporting." : "Run analysis again, or create a clip manually from the transcript.";
    cta = <Button variant="primary" onClick={() => onNavigate("clips")}>Review clips</Button>;
  }

  return (
    <div className="page">
      <header className="page-header">
        <div><p className="eyebrow">Overview</p><h1>{project.name}</h1><p>{project.source?.filename ?? "No source selected"}</p></div>
        <Status ready={project.status !== "source_missing"}>{project.status === "source_missing" ? "Source unavailable" : "Autosaved"}</Status>
      </header>

      <WorkflowSteps steps={steps} />

      <div className="overview-grid">
        <Panel className="overview-primary">
          <p className="overview-primary__eyebrow">Next step</p>
          <h2>{title}</h2>
          <p>{description}</p>
          {cta}
        </Panel>

        <Panel title="Source media" className="source-panel">
          {project.source ? (
            <div className="source-summary">
              {project.source.available ? (
                <>
                  <VideoPreview path={project.source.preview_path ?? project.source.path} />
                  <Button variant="quiet" disabled={Boolean(activeJob)} onClick={() => onStartJob("proxy")}>{project.source.preview_path ? "Preview proxy ready" : "Generate preview proxy"}</Button>
                </>
              ) : <div className="source-summary__missing" aria-hidden="true">▶</div>}
              <div className="source-summary__grid">
                <div><span>File</span><strong>{project.source.filename}</strong></div>
                <div><span>Duration</span><strong>{formatDuration(project.source.duration_seconds)}</strong></div>
                <div><span>Frame</span><strong>{project.source.width} × {project.source.height}</strong></div>
                <div><span>Rate</span><strong>{project.source.frame_rate.toFixed(2)} fps</strong></div>
                <div><span>Video</span><strong>{project.source.codec}</strong></div>
                <div><span>Audio</span><strong>{project.source.has_audio ? "Present" : "Not detected"}</strong></div>
              </div>
              {!project.source.available ? <div className="notice notice--warning"><strong>Source media unavailable</strong><span>Locate the original file to continue. AutoClip will not silently replace it.</span><Button onClick={onChooseSource}>Locate file</Button></div> : null}
            </div>
          ) : <EmptyState title="No source yet" description="Choose local footage to inspect its timing, streams, and processing options." action={<Button variant="primary" onClick={onChooseSource}>Select video</Button>} />}
        </Panel>

        <Panel title="Recent activity" className="jobs-panel">
          <JobList jobs={jobs} onCancel={onCancelJob} />
        </Panel>
      </div>
    </div>
  );
}

function JobList({ jobs, onCancel }: { jobs: Job[]; onCancel: (id: string) => void }) {
  if (!jobs.length) return <EmptyState title="No processing yet" description="Jobs will appear here with progress, recovery guidance, and results." />;
  return (
    <div className="job-list">
      {jobs.slice(0, 5).map((job) => (
        <article className="job-row" key={job.id}>
          <div className="job-row__top">
            <div><strong>{job.type.replace(/_/g, " ")}</strong><span>{job.current_stage}</span></div>
            <Status ready={job.state === "completed"}>{job.state}</Status>
          </div>
          {job.state === "running" || job.state === "queued" ? <Progress value={job.progress} /> : null}
          {job.error ? <p className="error-copy">{job.error.message}</p> : null}
          {job.cancellable && (job.state === "running" || job.state === "queued") ? <Button variant="quiet" onClick={() => onCancel(job.id)}>Cancel safely</Button> : null}
        </article>
      ))}
    </div>
  );
}
