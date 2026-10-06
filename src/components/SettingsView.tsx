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

type SettingsViewProps = {
  settings: Settings;
  doctor: Doctor | null;
  onSave: (settings: Partial<Settings>) => Promise<void>;
  onSetGeminiApiKey: (apiKey: string) => Promise<void>;
  onClearGeminiApiKey: () => Promise<void>;
  onClearCache: () => Promise<{ removed_bytes: number }>;
  onExportDiagnostics: () => Promise<{ path: string }>;
};

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

export function SettingsView({ settings, doctor, onSave, onSetGeminiApiKey, onClearGeminiApiKey, onClearCache, onExportDiagnostics }: SettingsViewProps) {
  const [draft, setDraft] = useState(settings);
  const [saved, setSaved] = useState(false);
  const [section, setSection] = useState<Section>("general");
  const [apiKey, setApiKey] = useState("");
  const [working, setWorking] = useState(false);
  const [result, setResult] = useState("");
  useEffect(() => setDraft(settings), [settings]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    await onSave(draft);
    setSaved(true);
    window.setTimeout(() => setSaved(false), 1600);
  }

  async function storeApiKey() {
    setWorking(true);
    setResult("");
    try {
      await onSetGeminiApiKey(apiKey);
      setApiKey("");
      setResult("Gemini API key stored securely for this Windows account.");
    } catch { setResult("The API key could not be stored. Review the error above and try again."); }
    finally { setWorking(false); }
  }

  async function clearApiKey() {
    setWorking(true);
    setResult("");
    try {
      await onClearGeminiApiKey();
      setApiKey("");
      setResult("Gemini API key removed.");
    } catch { setResult("The API key could not be removed. Review the error above and try again."); }
    finally { setWorking(false); }
  }

  async function cleanCache() {
    setWorking(true);
    setResult("");
    try {
      const response = await onClearCache();
      setResult(`Cleared ${formatBytes(response.removed_bytes)} of regenerable cache files. Projects and exports were preserved.`);
    } catch { setResult("The cache could not be cleared. Review the error above and try again."); }
    finally { setWorking(false); }
  }

  async function createDiagnosticBundle() {
    setWorking(true);
    setResult("");
    try {
      const response = await onExportDiagnostics();
      setResult(`Diagnostics saved to ${response.path}`);
    } catch { setResult("The diagnostic bundle could not be created. Review the error above and try again."); }
    finally { setWorking(false); }
  }

  const capabilities: Array<[string, { ready: boolean; summary: string }]> = doctor
    ? [["Desktop engine", doctor.runtime], ["Application storage", doctor.storage], ["FFmpeg", doctor.ffmpeg], ["FFprobe", doctor.ffprobe], ["Whisper", doctor.whisper], ["Speech model", doctor.model], ["Visual detector", doctor.mediapipe], ["OpenCV fallback", doctor.opencv], ["OpenTimelineIO", doctor.otio], ["Gemini", doctor.gemini], ["GPU acceleration", doctor.acceleration], ["Packaged Python", { ready: true, summary: doctor.python }]]
    : [];

  return <form className="page settings-page" onSubmit={submit}>
    <header className="page-header"><div><p className="eyebrow">Settings</p><h1>Configure AutoClip.</h1><p>Processing tools and private credentials stay under your Windows account.</p></div><Button variant="primary" type="submit">{saved ? "Saved" : "Save settings"}</Button></header>

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
          <Field label="Gemini API key" hint="Encrypted with Windows Data Protection and available only to your Windows account."><Input type="password" autoComplete="off" spellCheck={false} value={apiKey} placeholder={settings.gemini_configured ? "Configured — enter a new key to replace it" : "Paste an API key"} onChange={(event) => setApiKey(event.target.value)} /></Field>
          <div className="settings-actions"><Button type="button" variant="primary" disabled={working || !apiKey.trim()} onClick={() => { void storeApiKey(); }}>Save API key</Button><Button type="button" disabled={working || !settings.gemini_configured} onClick={() => { void clearApiKey(); }}>Remove API key</Button></div>
          <div className="setting-status"><span>Configuration</span><Status ready={settings.gemini_configured}>{settings.gemini_configured ? "Securely configured" : "Optional — not configured"}</Status></div>
          <p className="muted">The key is never written to project data, settings, logs, or diagnostic bundles.</p>
        </div> : null}

        {section === "diagnostics" ? <div className="diagnostics-stack">
          <div className="doctor-grid">{capabilities.length ? capabilities.map(([name, capability]) => <div className="doctor-row" key={name}><span>{name}</span><Status ready={capability.ready}>{capability.summary}</Status></div>) : <p className="muted">Checking system components…</p>}</div>
          {doctor ? <dl className="diagnostic-paths"><div><dt>AutoClip version</dt><dd>{doctor.app_version}</dd></div><div><dt>Data</dt><dd>{doctor.paths.data}</dd></div><div><dt>Cache</dt><dd>{doctor.paths.cache}</dd></div><div><dt>Models</dt><dd>{doctor.paths.models}</dd></div><div><dt>Logs</dt><dd>{doctor.paths.logs}</dd></div></dl> : null}
          <div className="settings-actions"><Button type="button" disabled={working} onClick={() => { void createDiagnosticBundle(); }}>Export diagnostics</Button><Button type="button" disabled={working} onClick={() => { void cleanCache(); }}>Clear cache</Button></div>
          <p className="muted">Diagnostics contain runtime status and sanitized logs, not project media, transcripts, or API keys. Clearing cache preserves projects and completed exports.</p>
        </div> : null}
        {result ? <p className="settings-result" role="status" aria-live="polite">{result}</p> : null}
      </div>
    </div>
  </form>;
}
