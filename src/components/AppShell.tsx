import type { ReactNode } from "react";

export type View = "home" | "overview" | "transcript" | "clips" | "exports" | "settings";

const PROJECT_NAV: Array<{ id: View; label: string }> = [
  { id: "overview", label: "Overview" },
  { id: "transcript", label: "Transcript" },
  { id: "clips", label: "Clips" },
  { id: "exports", label: "Exports" },
];

export function AppShell({ activeView, projectName, projectNeedsAttention, systemReady, onNavigate, children }: {
  activeView: View;
  projectName?: string;
  /** True when the open project needs attention (e.g. source media missing). */
  projectNeedsAttention?: boolean;
  /** True/false once known; undefined while still checking. Drives the quiet system-status indicator. */
  systemReady?: boolean;
  onNavigate: (view: View) => void;
  children: ReactNode;
}) {
  const hasProject = Boolean(projectName);
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand__mark">A</span><span>AutoClip</span></div>

        <nav aria-label="Primary navigation" className="sidebar-nav">
          <button className={activeView === "home" ? "nav-link nav-link--active" : "nav-link"} onClick={() => onNavigate("home")}>Projects</button>
        </nav>

        {hasProject ? (
          <div className="project-nav">
            <div className="project-identity" title={projectName}>
              <span className="project-identity__label">Project</span>
              <strong className="project-identity__name">{projectName}</strong>
              {projectNeedsAttention ? <span className="project-identity__flag">Needs attention</span> : null}
            </div>
            <nav aria-label="Project navigation" className="sidebar-nav">
              {PROJECT_NAV.map((item) => (
                <button key={item.id} className={activeView === item.id ? "nav-link nav-link--active" : "nav-link"} onClick={() => onNavigate(item.id)}>{item.label}</button>
              ))}
            </nav>
          </div>
        ) : null}

        <div className="sidebar-spacer" />

        <nav aria-label="Application navigation" className="sidebar-nav">
          <button className={activeView === "settings" ? "nav-link nav-link--active" : "nav-link"} onClick={() => onNavigate("settings")}>Settings</button>
        </nav>

        <button type="button" className="sidebar-status" onClick={() => onNavigate("settings")} title="Open System Doctor in Settings">
          <span className={`sidebar-status__dot ${systemReady ? "sidebar-status__dot--ready" : "sidebar-status__dot--warning"}`} aria-hidden="true" />
          {systemReady === undefined ? "Checking system…" : systemReady ? "System ready" : "Setup needed"}
        </button>
      </aside>
      <main className="content">{children}</main>
    </div>
  );
}
