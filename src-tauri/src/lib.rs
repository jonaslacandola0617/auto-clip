use serde_json::Value;
use std::{
    env,
    fs::{self, OpenOptions},
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
    process::{Child, ChildStdin, ChildStdout, Command, Stdio},
    sync::{Arc, Mutex},
};
use tauri::Manager;

#[cfg(windows)]
use std::os::windows::{io::AsRawHandle, process::CommandExt};
#[cfg(windows)]
use windows_sys::Win32::{
    Foundation::CloseHandle,
    System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, TerminateJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    },
};

struct WorkerProcess {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
    #[cfg(windows)]
    job_handle: isize,
}

impl Drop for WorkerProcess {
    fn drop(&mut self) {
        #[cfg(windows)]
        unsafe {
            if self.job_handle != 0 {
                TerminateJobObject(self.job_handle as _, 1);
                CloseHandle(self.job_handle as _);
            }
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[derive(Clone)]
struct WorkerManager {
    process: Arc<Mutex<Option<WorkerProcess>>>,
    repository_root: PathBuf,
    resource_dir: PathBuf,
    app_data_dir: PathBuf,
}

impl WorkerManager {
    fn new(resource_dir: PathBuf, app_data_dir: PathBuf) -> Self {
        let repository_root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("src-tauri must be inside the repository")
            .to_path_buf();
        Self { process: Arc::new(Mutex::new(None)), repository_root, resource_dir, app_data_dir }
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

    #[cfg(windows)]
    fn assign_job(child: &Child) -> Result<isize, String> {
        unsafe {
            let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if job.is_null() {
                return Err("Could not create the worker process group.".to_string());
            }
            let mut information: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            information.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if SetInformationJobObject(
                job,
                JobObjectExtendedLimitInformation,
                &information as *const _ as *const _,
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            ) == 0 || AssignProcessToJobObject(job, child.as_raw_handle() as _) == 0
            {
                CloseHandle(job);
                return Err("Could not attach the processing worker to AutoClip's process group.".to_string());
            }
            Ok(job as isize)
        }
    }

    fn spawn_worker(&self) -> Result<WorkerProcess, String> {
        fs::create_dir_all(self.app_data_dir.join("logs")).map_err(|error| format!("Could not create the AutoClip log directory: {error}"))?;
        let stderr = OpenOptions::new().create(true).append(true).open(self.app_data_dir.join("logs").join("worker.log"))
            .map_err(|error| format!("Could not open the processing log: {error}"))?;
        let release = !cfg!(debug_assertions);
        let worker_root = self.resource_dir.join("runtime").join("worker");
        let mut command = if release {
            Command::new(worker_root.join("autoclip-worker.exe"))
        } else {
            Command::new(Self::python_path(&self.repository_root))
        };
        if release {
            command.arg("--stdio").current_dir(&worker_root).env("AUTOCLIP_RELEASE", "1");
        } else {
            command.args(["-m", "autoclip.desktop_worker", "--stdio"])
                .current_dir(&self.repository_root)
                .env("PYTHONPATH", self.repository_root.join("src"));
        }
        command
            .env("AUTOCLIP_APP_VERSION", env!("CARGO_PKG_VERSION"))
            .env("AUTOCLIP_APP_DATA", &self.app_data_dir)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::from(stderr));
        #[cfg(windows)]
        command.creation_flags(0x08000000);
        let mut child = command.spawn().map_err(|error| format!("Could not start the AutoClip processing worker: {error}"))?;
        #[cfg(windows)]
        let job_handle = match Self::assign_job(&child) {
            Ok(handle) => handle,
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(error);
            }
        };
        let stdin = child.stdin.take().ok_or_else(|| "The processing worker did not expose input.".to_string())?;
        let stdout = child.stdout.take().ok_or_else(|| "The processing worker did not expose output.".to_string())?;
        Ok(WorkerProcess { child, stdin, stdout: BufReader::new(stdout), #[cfg(windows)] job_handle })
    }

    fn exchange(worker: &mut WorkerProcess, request: &Value) -> Result<Value, String> {
        let mut serialized = serde_json::to_string(request).map_err(|error| format!("Could not serialize the desktop request: {error}"))?;
        serialized.push('\n');
        worker.stdin.write_all(serialized.as_bytes()).and_then(|_| worker.stdin.flush()).map_err(|error| format!("Could not send the desktop request: {error}"))?;
        let mut response = String::new();
        worker.stdout.read_line(&mut response).map_err(|error| format!("Could not read the processing response: {error}"))?;
        if response.trim().is_empty() {
            return Err("The processing worker stopped unexpectedly. Your project remains saved; try the action again or export diagnostics from Settings.".to_string());
        }
        serde_json::from_str(&response).map_err(|error| format!("The processing worker returned an invalid response: {error}"))
    }

    fn send(&self, request: Value) -> Result<Value, String> {
        let mut guard = self.process.lock().map_err(|_| "The processing worker lock is unavailable.".to_string())?;
        if guard.is_none() {
            *guard = Some(self.spawn_worker()?);
        }
        let result = Self::exchange(guard.as_mut().expect("worker initialized"), &request);
        if result.is_err() {
            *guard = None;
        }
        result
    }

    fn shutdown(&self) {
        if let Ok(mut guard) = self.process.lock() {
            *guard = None;
        }
    }
}

#[tauri::command]
async fn worker_request(request: Value, state: tauri::State<'_, WorkerManager>) -> Result<Value, String> {
    let manager = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || manager.send(request))
        .await
        .map_err(|error| format!("The desktop request task failed: {error}"))?
}

#[tauri::command]
fn allow_media_path(path: PathBuf, app: tauri::AppHandle) -> Result<String, String> {
    let canonical = path.canonicalize().map_err(|_| "The preview media is unavailable. Locate the source file or regenerate the preview.".to_string())?;
    let extension = canonical.extension().and_then(|value| value.to_str()).unwrap_or("").to_ascii_lowercase();
    const PREVIEW_EXTENSIONS: &[&str] = &["mp4", "mov", "mkv", "avi", "webm", "m4v"];
    if !PREVIEW_EXTENSIONS.contains(&extension.as_str()) {
        return Err("AutoClip refused to preview an unsupported local file type.".to_string());
    }
    app.asset_protocol_scope().allow_file(&canonical)
        .map_err(|error| format!("AutoClip could not authorize this preview file: {error}"))?;
    Ok(canonical.to_string_lossy().into_owned())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let resource_dir = app.path().resource_dir().map_err(|error| format!("Could not resolve AutoClip resources: {error}"))?;
            let app_data_dir = app.path().local_data_dir()
                .map_err(|error| format!("Could not resolve Windows local application data: {error}"))?
                .join("com.autoclip.desktop");
            fs::create_dir_all(&app_data_dir).map_err(|error| format!("Could not create AutoClip application data: {error}"))?;
            app.manage(WorkerManager::new(resource_dir, app_data_dir));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![worker_request, allow_media_path])
        .build(tauri::generate_context!())
        .expect("error while building AutoClip");
    app.run(|app_handle, event| {
        if matches!(event, tauri::RunEvent::Exit | tauri::RunEvent::ExitRequested { .. }) {
            app_handle.state::<WorkerManager>().shutdown();
        }
    });
}

