import { useCallback, useEffect, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { AppShell, type View } from "./components/AppShell";
import { ClipsView } from "./components/ClipsView";
import { EmptyState } from "./components/Ui";
import { ExportsView } from "./components/ExportsView";
import { HomeView } from "./components/HomeView";
import { OverviewView } from "./components/OverviewView";
import { ProjectDialog, type NewProjectValues } from "./components/ProjectDialog";
import { SettingsView } from "./components/SettingsView";
import { TranscriptView } from "./components/TranscriptView";
import { AutoClipError, workerRequest } from "./lib/worker";
import type { Doctor, Job, ProjectState, RecentProject, Settings } from "./types";

export function App() {
  const [view, setView] = useState<View>("home");
  const [doctor, setDoctor] = useState<Doctor | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [recents, setRecents] = useState<RecentProject[]>([]);
  const [project, setProject] = useState<ProjectState | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [showNewProject, setShowNewProject] = useState(false);
  const [error, setError] = useState<{ message: string; details?: string } | null>(null);

  const reportError = useCallback((reason: unknown) => {
    const info = reason instanceof AutoClipError ? reason.info : null;
    setError({ message: info?.message ?? "AutoClip couldn't complete this action.", details: info?.details });
  }, []);

  const refreshRecents = useCallback(async () => setRecents(await workerRequest<RecentProject[]>("list_recent_projects")), []);
  const refreshEnvironment = useCallback(async () => {
    const [doctorState, settingsState] = await Promise.all([workerRequest<Doctor>("doctor"), workerRequest<Settings>("get_settings")]);
    setDoctor(doctorState);
    setSettings(settingsState);
    document.documentElement.dataset.theme = settingsState.theme;
  }, []);
  const refreshJobs = useCallback(async (projectPath: string) => setJobs(await workerRequest<Job[]>("list_jobs", { project_path: projectPath })), []);
  const refreshProject = useCallback(async (projectPath: string) => setProject(await workerRequest<ProjectState>("get_project_state", { path: projectPath })), []);

  const watchJob = useCallback((jobId: string, watchedProjectPath: string) => {
    let attempts = 0;
    const poll = async () => {
      try {
        const job = await workerRequest<Job>("get_job", { job_id: jobId });
        setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
        await refreshProject(watchedProjectPath);
        if (["queued", "waiting", "running", "cancelling"].includes(job.state) && attempts++ < 7200) window.setTimeout(() => { void poll(); }, 500);
        else await refreshJobs(watchedProjectPath);
      } catch (reason) { reportError(reason); }
    };
    window.setTimeout(() => { void poll(); }, 200);
  }, [refreshJobs, refreshProject, reportError]);

  useEffect(() => {
    Promise.all([workerRequest<Doctor>("doctor"), workerRequest<Settings>("get_settings"), workerRequest<RecentProject[]>("list_recent_projects")])
      .then(([doctorState, settingsState, recentState]) => { setDoctor(doctorState); setSettings(settingsState); setRecents(recentState); document.documentElement.dataset.theme = settingsState.theme; })
      .catch(reportError)
      .finally(() => setLoading(false));
  }, [reportError]);

  const hasActiveJob = jobs.some((job) => ["queued", "waiting", "running", "cancelling"].includes(job.state));
  const projectPath = project?.path;
  useEffect(() => {
    if (!projectPath || !hasActiveJob) return;
    const timer = window.setInterval(() => {
      void Promise.all([refreshJobs(projectPath), refreshProject(projectPath)]).catch(reportError);
    }, 900);
    return () => window.clearInterval(timer);
  }, [hasActiveJob, projectPath, refreshJobs, refreshProject, reportError]);

  async function openProject(path: string) {
    try {
      const opened = await workerRequest<ProjectState>("open_project", { path });
      setProject(opened); setView("overview"); setError(null);
      await Promise.all([refreshRecents(), refreshJobs(opened.path)]);
    } catch (reason) { reportError(reason); }
  }

  async function chooseProject() {
    const selected = await open({ multiple: false, title: "Open AutoClip project", filters: [{ name: "AutoClip Project", extensions: ["json"] }] });
    if (typeof selected === "string") await openProject(selected);
  }

  async function createProject(values: NewProjectValues) {
    try {
      const created = await workerRequest<ProjectState>("create_project", values);
      setProject(created); setJobs([]); setShowNewProject(false); setView("overview"); setError(null);
      await refreshRecents();
    } catch (reason) { reportError(reason); }
  }

  async function chooseSource() {
    if (!project) return;
    const selected = await open({ multiple: false, title: project.status === "source_missing" ? "Locate source video" : "Select source video", filters: [{ name: "Video", extensions: ["mp4", "mov", "mkv", "avi", "webm", "m4v"] }] });
    if (typeof selected !== "string") return;
    try {
      const updated = await workerRequest<ProjectState>("relink_source", { project_path: project.path, source_path: selected });
      setProject(updated); setError(null);
    } catch (reason) { reportError(reason); }
  }

  async function startProjectJob(type: string, payload: Record<string, unknown> = {}) {
    if (!project) return;
    try {
      const started = await workerRequest<Job>("start_job", { type, project_path: project.path, ...payload });
      setJobs((current) => [started, ...current.filter((job) => job.id !== started.id)]);
      watchJob(started.id, project.path);
    } catch (reason) { reportError(reason); }
  }

  async function mutateProject(command: string, payload: Record<string, unknown>) {
    if (!project) return;
    try {
      const updated = await workerRequest<ProjectState>(command, { project_path: project.path, ...payload });
      setProject(updated);
      setError(null);
    } catch (reason) { reportError(reason); }
  }

  async function cancelJob(jobId: string) {
    try {
      await workerRequest<Job>("cancel_job", { job_id: jobId });
      if (project) await refreshJobs(project.path);
    } catch (reason) { reportError(reason); }
  }

  async function saveSettings(next: Partial<Settings>) {
    try {
      const saved = await workerRequest<Settings>("update_settings", next);
      setSettings(saved); document.documentElement.dataset.theme = saved.theme;
    } catch (reason) { reportError(reason); }
  }

  async function setGeminiApiKey(apiKey: string) {
    try {
      await workerRequest("set_gemini_api_key", { api_key: apiKey });
      await refreshEnvironment();
      setError(null);
    } catch (reason) { reportError(reason); throw reason; }
  }

  async function clearGeminiApiKey() {
    try {
      await workerRequest("clear_gemini_api_key");
      await refreshEnvironment();
      setError(null);
    } catch (reason) { reportError(reason); throw reason; }
  }

  async function clearCache() {
    try {
      return await workerRequest<{ removed_bytes: number }>("clear_cache", project ? { project_path: project.path } : {});
    } catch (reason) { reportError(reason); throw reason; }
  }

  async function exportDiagnostics() {
    try {
      return await workerRequest<{ path: string }>("export_diagnostics");
    } catch (reason) { reportError(reason); throw reason; }
  }

  async function removeRecent(path: string) {
    try { setRecents(await workerRequest<RecentProject[]>("remove_recent_project", { path })); } catch (reason) { reportError(reason); }
  }

  async function relinkEnhancementAsset(editSequenceId: string, assetId: string) {
    const selected = await open({ multiple: false, title: "Locate enhancement asset", filters: [{ name: "Media", extensions: ["mp4", "mov", "mkv", "webm", "png", "jpg", "jpeg", "webp", "wav", "mp3", "m4a", "aac", "flac", "ogg"] }] });
    if (typeof selected === "string") await mutateProject("update_enhancement_plan", { edit_sequence_id: editSequenceId, operation: "relink", asset_id: assetId, replacement_path: selected });
  }

  const systemReady = doctor ? doctor.ffmpeg.ready && doctor.whisper.ready : undefined;
  const sourcePath = project?.source ? project.source.preview_path ?? project.source.path : null;

  return <AppShell activeView={view} projectName={project?.name} projectNeedsAttention={project ? project.status === "source_missing" : undefined} systemReady={systemReady} onNavigate={setView}>
    {error ? <div className="error-banner" role="alert"><div><strong>{error.message}</strong>{error.details ? <details><summary>View details</summary><pre>{error.details}</pre></details> : null}</div><button aria-label="Dismiss error" onClick={() => setError(null)}>×</button></div> : null}

    {view === "home" ? <HomeView doctor={doctor} recents={recents} loading={loading} onNew={() => setShowNewProject(true)} onOpen={chooseProject} onOpenRecent={openProject} onRemoveRecent={removeRecent} onOpenSettings={() => setView("settings")} /> : null}

    {view === "overview" && project ? <OverviewView project={project} doctor={doctor} jobs={jobs} onChooseSource={chooseSource} onStartJob={startProjectJob} onCancelJob={cancelJob} onNavigate={setView} /> : null}

    {view === "transcript" && project ? <TranscriptView transcript={project.transcript} sourcePath={sourcePath} busy={hasActiveJob} onTranscribe={() => { void startProjectJob("transcribe"); }} onCorrect={(segmentId, text) => mutateProject("correct_transcript", { segment_id: segmentId, text })} onCreateClip={(startSegmentId, endSegmentId) => mutateProject("create_manual_clip", { start_segment_id: startSegmentId, end_segment_id: endSegmentId })} /> : null}

    {view === "clips" && project ? (project.source ? <ClipsView candidates={project.candidates} clips={project.clips} storyConcepts={project.story_concepts} editSequences={project.edit_sequences} workflowProfiles={project.workflow_profiles} campaignProfiles={project.campaign_profiles} productionRuns={project.production_runs} sourcePath={project.source.preview_path ?? project.source.path} geminiReady={Boolean(doctor?.gemini.ready)} busy={hasActiveJob} analysisJob={jobs.find((job) => job.type === "analyze") ?? null} smartEditJob={jobs.find((job) => job.type === "smart_edit") ?? null} onCommand={mutateProject} onStartJob={startProjectJob} onRelinkEnhancementAsset={relinkEnhancementAsset} /> : <EmptyState title="Select a source video" description="A source is required before clips can be created." />) : null}

    {view === "exports" && project ? <ExportsView clips={project.clips} editSequences={project.edit_sequences} productionRuns={project.production_runs} outputs={project.outputs} busy={hasActiveJob} onStartJob={startProjectJob} /> : null}

    {view === "settings" && settings ? <SettingsView settings={settings} doctor={doctor} onSave={saveSettings} onSetGeminiApiKey={setGeminiApiKey} onClearGeminiApiKey={clearGeminiApiKey} onClearCache={clearCache} onExportDiagnostics={exportDiagnostics} /> : null}

    {showNewProject && settings ? <ProjectDialog defaultLocation={settings.default_project_directory} onClose={() => setShowNewProject(false)} onCreate={createProject} /> : null}
  </AppShell>;
}
