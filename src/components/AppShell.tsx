import type { ReactNode } from "react";

type View = "home" | "workspace" | "settings";

export function AppShell({ activeView, projectName, onNavigate, children }: { activeView: View; projectName?: string; onNavigate: (view: View) => void; children: ReactNode }) {
  const items: Array<{ id: View; label: string; meta?: string }> = [
    { id: "home", label: "Projects" },
    { id: "workspace", label: "Workspace", meta: projectName },
    { id: "settings", label: "Settings" },
  ];
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand__mark">A</span><span>AutoClip</span></div>
        <nav aria-label="Primary navigation">
          {items.map((item) => (
            <button key={item.id} className={activeView === item.id ? "nav-item nav-item--active" : "nav-item"} onClick={() => onNavigate(item.id)} disabled={item.id === "workspace" && !projectName}>
              <span>{item.label}</span>{item.meta ? <small>{item.meta}</small> : null}
            </button>
          ))}
        </nav>
        <div className="sidebar__footer"><span className="gate-dot" />Phase 1A desktop foundation</div>
      </aside>
      <main className="content">{children}</main>
    </div>
  );
}

