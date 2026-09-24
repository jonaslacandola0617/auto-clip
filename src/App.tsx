import { useCallback, useEffect, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { AppShell } from "./components/AppShell";
import { HomeView } from "./components/HomeView";
import { ProjectDialog, type NewProjectValues } from "./components/ProjectDialog";
import { SettingsView } from "./components/SettingsView";
import { WorkspaceView } from "./components/WorkspaceView";
import { AutoClipError, workerRequest } from "./lib/worker";
import type { Doctor, Job, ProjectState, RecentProject, Settings } from "./types";

type View = "home" | "workspace" | "settings";

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
  const refreshJobs = useCallback(async (projectPath: string) => setJobs(await workerRequest<Job[]>("list_jobs", { project_path: projectPath })), []);

  useEffect(() => {
    Promise.all([workerRequest<Doctor>("doctor"), workerRequest<Settings>("get_settings"), workerRequest<RecentProject[]>("list_recent_projects")])
      .then(([doctorState, settingsState, recentState]) => { setDoctor(doctorState); setSettings(settingsState); setRecents(recentState); document.documentElement.dataset.theme = settingsState.theme; })
      .catch(reportError)
      .finally(() => setLoading(false));
  }, [reportError]);

  const hasActiveJob = jobs.some((job) => job.state === "queued" || job.state === "running");
  const projectPath = project?.path;
  useEffect(() => {
    if (!projectPath || !hasActiveJob) return;
    const timer = window.setInterval(() => {
      void refreshJobs(projectPath).then(async () => {
        const current = await workerRequest<ProjectState>("get_project_state", { path: projectPath });
        setProject(current);
      }).catch(reportError);
    }, 900);
    return () => window.clearInterval(timer);
  }, [hasActiveJob, projectPath, refreshJobs, reportError]);

  async function openProject(path: string) {
    try {
      const opened = await workerRequest<ProjectState>("open_project", { path });
      setProject(opened); setView("workspace"); setError(null);
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
      setProject(created); setJobs([]); setShowNewProject(false); setView("workspace"); setError(null);
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
      await workerRequest<Job>("start_job", { type, project_path: project.path, ...payload });
      await refreshJobs(project.path);
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

  async function removeRecent(path: string) {
    try { setRecents(await workerRequest<RecentProject[]>("remove_recent_project", { path })); } catch (reason) { reportError(reason); }
  }

  return <AppShell activeView={view} projectName={project?.name} onNavigate={setView}>
    {error ? <div className="error-banner" role="alert"><div><strong>{error.message}</strong>{error.details ? <details><summary>View details</summary><pre>{error.details}</pre></details> : null}</div><button aria-label="Dismiss error" onClick={() => setError(null)}>×</button></div> : null}
    {view === "home" ? <HomeView doctor={doctor} recents={recents} loading={loading} onNew={() => setShowNewProject(true)} onOpen={chooseProject} onOpenRecent={openProject} onRemoveRecent={removeRecent} /> : null}
    {view === "workspace" && project ? <WorkspaceView project={project} doctor={doctor} jobs={jobs} onChooseSource={chooseSource} onStartJob={startProjectJob} onCommand={mutateProject} onCancelJob={cancelJob} /> : null}
    {view === "settings" && settings ? <SettingsView settings={settings} doctor={doctor} onSave={saveSettings} /> : null}
    {showNewProject && settings ? <ProjectDialog defaultLocation={settings.default_project_directory} onClose={() => setShowNewProject(false)} onCreate={createProject} /> : null}
  </AppShell>;
}
