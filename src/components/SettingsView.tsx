import { useEffect, useState } from "react";
import type { Doctor, Settings } from "../types";
import { Button, Field, Input, Select, Status } from "./Ui";

type Section = "general" | "processing" | "ai" | "diagnostics";
const SECTIONS: Array<{ id: Section; label: string }> = [
  { id: "general", label: "General" },
  { id: "processing", label: "Processing" },
  { id: "ai", label: "AI" },
  { id: "diagnostics", label: "Diagnostics" },
];

export function SettingsView({ settings, doctor, onSave }: { settings: Settings; doctor: Doctor | null; onSave: (settings: Partial<Settings>) => Promise<void> }) {
  const [draft, setDraft] = useState(settings);
  const [saved, setSaved] = useState(false);
  const [section, setSection] = useState<Section>("general");
  useEffect(() => setDraft(settings), [settings]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    await onSave(draft);
    setSaved(true);
    window.setTimeout(() => setSaved(false), 1600);
  }

  const capabilities: Array<[string, { ready: boolean; summary: string }]> = doctor
    ? [["FFmpeg", doctor.ffmpeg], ["FFprobe", doctor.ffprobe], ["Whisper", doctor.whisper], ["Visual detector", doctor.mediapipe], ["OpenCV fallback", doctor.opencv], ["OpenTimelineIO", doctor.otio], ["Gemini", doctor.gemini], ["GPU acceleration", doctor.acceleration], ["Desktop worker", { ready: true, summary: `Python ${doctor.python}` }]]
    : [];

  return <form className="page settings-page" onSubmit={submit}>
    <header className="page-header"><div><p className="eyebrow">Settings</p><h1>Keep the defaults sensible.</h1><p>Only the controls needed for this desktop foundation are exposed.</p></div><Button variant="primary" type="submit">{saved ? "Saved" : "Save settings"}</Button></header>

    <div className="settings-layout">
      <nav className="settings-nav" aria-label="Settings sections">{SECTIONS.map((item) => <button key={item.id} type="button" className={section === item.id ? "settings-nav__item settings-nav__item--active" : "settings-nav__item"} onClick={() => setSection(item.id)}>{item.label}</button>)}</nav>

      <div className="settings-content">
        {section === "general" ? <div className="field-stack">
          <Field label="Default project directory"><Input value={draft.default_project_directory} onChange={(event) => setDraft({ ...draft, default_project_directory: event.target.value })} /></Field>
          <Field label="Default export directory" hint="Leave blank to use each project's exports folder."><Input value={draft.default_export_directory} onChange={(event) => setDraft({ ...draft, default_export_directory: event.target.value })} /></Field>
          <Field label="Theme"><Select value={draft.theme} onChange={(event) => setDraft({ ...draft, theme: event.target.value as Settings["theme"] })}><option value="dark">Dark</option><option value="light">Light</option></Select></Field>
        </div> : null}

        {section === "processing" ? <div className="field-stack">
          <Field label="Whisper model" hint="Base is the CPU default. Small and Medium trade longer processing for accuracy."><Select value={draft.whisper_model} onChange={(event) => setDraft({ ...draft, whisper_model: event.target.value as Settings["whisper_model"] })}><option value="tiny">Tiny</option><option value="base">Base</option><option value="small">Small</option><option value="medium">Medium</option></Select></Field>
          <Field label="Processing device"><Input value="CPU" disabled /></Field>
        </div> : null}

        {section === "ai" ? <div className="field-stack">
          <Field label="Provider"><Input value="Gemini" disabled /></Field>
          <Field label="Model"><Input value={draft.gemini_model} onChange={(event) => setDraft({ ...draft, gemini_model: event.target.value })} /></Field>
          <div className="setting-status"><span>Configuration</span><Status ready={settings.gemini_configured}>{settings.gemini_configured ? "Configured from environment" : "API key not configured"}</Status></div>
          <p className="muted">For development, add GEMINI_API_KEY to the environment or the project-root .env file. The key is never saved in settings or project data.</p>
        </div> : null}

        {section === "diagnostics" ? <div className="doctor-grid">{capabilities.length ? capabilities.map(([name, capability]) => <div className="doctor-row" key={name}><span>{name}</span><Status ready={capability.ready}>{capability.summary}</Status></div>) : <p className="muted">Checking system components…</p>}</div> : null}
      </div>
    </div>
  </form>;
}
