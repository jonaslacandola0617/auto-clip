import { useEffect, useState } from "react";
import type { Doctor, Settings } from "../types";
import { Button, Field, Input, Panel, Select, Status } from "./Ui";

export function SettingsView({ settings, doctor, onSave }: { settings: Settings; doctor: Doctor | null; onSave: (settings: Partial<Settings>) => Promise<void> }) {
  const [draft, setDraft] = useState(settings);
  const [saved, setSaved] = useState(false);
  useEffect(() => setDraft(settings), [settings]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    await onSave(draft);
    setSaved(true);
    window.setTimeout(() => setSaved(false), 1600);
  }

  const capabilities = doctor ? [
    ["FFmpeg", doctor.ffmpeg], ["Whisper", doctor.whisper], ["OpenCV", doctor.opencv], ["MediaPipe", doctor.mediapipe], ["OpenTimelineIO", doctor.otio], ["Gemini", doctor.gemini], ["GPU acceleration", doctor.acceleration],
  ] as const : [];

  return <form className="page settings-page" onSubmit={submit}><header className="page-header"><div><p className="eyebrow">Settings</p><h1>Keep the defaults sensible.</h1><p>Only the controls needed for this desktop foundation are exposed.</p></div><Button variant="primary" type="submit">{saved ? "Saved" : "Save settings"}</Button></header>
    <div className="settings-grid"><Panel title="General"><div className="field-stack"><Field label="Default project directory"><Input value={draft.default_project_directory} onChange={(event) => setDraft({ ...draft, default_project_directory: event.target.value })} /></Field><Field label="Default export directory" hint="Leave blank to use each project's exports folder."><Input value={draft.default_export_directory} onChange={(event) => setDraft({ ...draft, default_export_directory: event.target.value })} /></Field><Field label="Theme"><Select value={draft.theme} onChange={(event) => setDraft({ ...draft, theme: event.target.value as Settings["theme"] })}><option value="dark">Dark</option><option value="light">Light</option></Select></Field></div></Panel>
      <Panel title="Processing"><div className="field-stack"><Field label="Whisper model" hint="Base is the CPU default. Small and Medium trade longer processing for accuracy."><Select value={draft.whisper_model} onChange={(event) => setDraft({ ...draft, whisper_model: event.target.value as Settings["whisper_model"] })}><option value="tiny">Tiny</option><option value="base">Base</option><option value="small">Small</option><option value="medium">Medium</option></Select></Field><Field label="Processing device"><Input value="CPU" disabled /></Field></div></Panel>
      <Panel title="AI"><div className="field-stack"><Field label="Provider"><Input value="Gemini" disabled /></Field><Field label="Model"><Input value={draft.gemini_model} onChange={(event) => setDraft({ ...draft, gemini_model: event.target.value })} /></Field><div className="setting-status"><span>Configuration</span><Status ready={settings.gemini_configured}>{settings.gemini_configured ? "Configured from environment" : "API key not configured"}</Status></div><p className="muted">For development, add GEMINI_API_KEY to the environment or the project-root .env file. The key is never saved in settings or project data.</p></div></Panel>
      <Panel title="System doctor" className="doctor-panel"><div className="doctor-list">{capabilities.map(([name, capability]) => <div key={name}><span>{name}</span><Status ready={capability.ready}>{capability.summary}</Status></div>)}</div></Panel>
    </div>
  </form>;
}
