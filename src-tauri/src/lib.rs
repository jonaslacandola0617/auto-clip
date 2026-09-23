use serde_json::Value;
use std::{
    env,
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
    process::{Child, ChildStdin, ChildStdout, Command, Stdio},
    sync::{Arc, Mutex},
};

struct WorkerProcess {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
}

impl Drop for WorkerProcess {
    fn drop(&mut self) {
        let _ = self.child.kill();
    }
}

#[derive(Clone, Default)]
struct WorkerManager {
    process: Arc<Mutex<Option<WorkerProcess>>>,
}

impl WorkerManager {
    fn repository_root() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("src-tauri must be inside the repository")
            .to_path_buf()
    }

    fn python_path(root: &Path) -> PathBuf {
        if let Some(configured) = env::var_os("AUTOCLIP_PYTHON") {
            return PathBuf::from(configured);
        }
        let local = root.join(".venv").join("Scripts").join("python.exe");
        if local.exists() {
            local
        } else {
            PathBuf::from("python")
        }
    }

    fn spawn_worker() -> Result<WorkerProcess, String> {
        let root = Self::repository_root();
        let mut command = Command::new(Self::python_path(&root));
        command
            .args(["-m", "autoclip.desktop_worker", "--stdio"])
            .current_dir(&root)
            .env("PYTHONPATH", root.join("src"))
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit());
        let mut child = command.spawn().map_err(|error| format!("Could not start the AutoClip processing worker: {error}"))?;
        let stdin = child.stdin.take().ok_or_else(|| "The processing worker did not expose input.".to_string())?;
        let stdout = child.stdout.take().ok_or_else(|| "The processing worker did not expose output.".to_string())?;
        Ok(WorkerProcess { child, stdin, stdout: BufReader::new(stdout) })
    }

    fn send(&self, request: Value) -> Result<Value, String> {
        let mut guard = self.process.lock().map_err(|_| "The processing worker lock is unavailable.".to_string())?;
        if guard.is_none() {
            *guard = Some(Self::spawn_worker()?);
        }
        let worker = guard.as_mut().expect("worker initialized");
        let mut serialized = serde_json::to_string(&request).map_err(|error| format!("Could not serialize the desktop request: {error}"))?;
        serialized.push('\n');
        worker.stdin.write_all(serialized.as_bytes()).and_then(|_| worker.stdin.flush()).map_err(|error| format!("Could not send the desktop request: {error}"))?;
        let mut response = String::new();
        worker.stdout.read_line(&mut response).map_err(|error| format!("Could not read the processing response: {error}"))?;
        if response.trim().is_empty() {
            *guard = None;
            return Err("The processing worker stopped unexpectedly. Try the action again.".to_string());
        }
        serde_json::from_str(&response).map_err(|error| format!("The processing worker returned an invalid response: {error}"))
    }
}

#[tauri::command]
async fn worker_request(request: Value, state: tauri::State<'_, WorkerManager>) -> Result<Value, String> {
    let manager = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || manager.send(request))
        .await
        .map_err(|error| format!("The desktop request task failed: {error}"))?
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(WorkerManager::default())
        .invoke_handler(tauri::generate_handler![worker_request])
        .run(tauri::generate_context!())
        .expect("error while running AutoClip");
}

