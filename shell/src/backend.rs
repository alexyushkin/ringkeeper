//! The backend (`ringkeeper desktop`): starting it, finding a running one,
//! talking to it with the launch secret, and stopping it.

use std::ffi::OsString;
use std::fs::{self, File};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use serde::Deserialize;
use serde_json::json;

use crate::logic::{Runtime, Status};

const RUNTIME_FILE: &str = "desktop.json";
/// The backend's exit code when another one already holds the data folder.
const ALREADY_RUNNING: i32 = 3;
/// First start migrates the database and makes the master key: give it time.
const START_TIMEOUT: Duration = Duration::from_secs(90);

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// How to run the backend: the program, its arguments, extra environment.
pub struct Launcher {
    pub program: PathBuf,
    pub args: Vec<OsString>,
    pub env: Vec<(String, String)>,
    pub data_dir: PathBuf,
}

impl Launcher {
    /// The installed app runs its embedded Python from the app's resources
    /// (laid out by the packaging step). A debug build runs the repository's
    /// own `.venv`, so `cargo run` works after `uv sync`.
    pub fn find(resources: Option<&Path>, data_dir: &Path) -> Result<Launcher, String> {
        let mut env = vec![
            ("RINGKEEPER_ENV_FILE".to_string(), String::new()),
            ("PYTHONUNBUFFERED".to_string(), "1".to_string()),
        ];
        let python = resources.map(|r| r.join("python").join(python_exe()));
        let web = resources.map(|r| r.join("web"));
        let (program, web) = match (python, web) {
            (Some(python), Some(web)) if python.is_file() => (python, Some(web)),
            _ if cfg!(debug_assertions) => {
                let repo = Path::new(env!("CARGO_MANIFEST_DIR")).join("..");
                let venv = repo.join(".venv");
                let python = if cfg!(windows) {
                    venv.join("Scripts").join("python.exe")
                } else {
                    venv.join("bin").join("python")
                };
                (python, Some(repo.join("web").join(".desktop").join("out")))
            }
            _ => return Err("Ringkeeper's files are incomplete; reinstall it.".into()),
        };
        if !program.is_file() {
            return Err(format!("no Python at {}", program.display()));
        }
        // Workers use the bundled git (MinGit on Windows) unless told otherwise.
        let git = resources.map(|r| r.join("mingit").join("cmd"));
        if let Some(git) = git.filter(|g| g.join("git.exe").is_file()) {
            if std::env::var_os("RINGKEEPER_WORKER_TOOLS_PATH").is_none() {
                let paths = serde_json::to_string(&[git.to_string_lossy()]).unwrap_or_default();
                env.push(("RINGKEEPER_WORKER_TOOLS_PATH".into(), paths));
            }
        }
        // Settings > About shows the installed third-party notices.
        if let Some(notices) = resources.map(|r| r.join("notices")).filter(|n| n.is_dir()) {
            env.push((
                "RINGKEEPER_NOTICES_DIR".into(),
                notices.to_string_lossy().into(),
            ));
        }
        let mut args: Vec<OsString> = vec!["-m".into(), "ringkeeper".into(), "desktop".into()];
        args.push("--data-dir".into());
        args.push(data_dir.into());
        if let Some(web) = web.filter(|w| w.join("index.html").is_file()) {
            args.push("--web-dir".into());
            args.push(web.into());
        }
        Ok(Launcher {
            program,
            args,
            env,
            data_dir: data_dir.to_path_buf(),
        })
    }

    fn spawn(&self) -> Result<Child, String> {
        let logs = self.data_dir.join("logs");
        fs::create_dir_all(&logs).map_err(|e| format!("can't create {}: {e}", logs.display()))?;
        let log = logs.join("backend.log");
        if log.exists() {
            let _ = fs::rename(&log, logs.join("backend.previous.log"));
        }
        let out = File::create(&log).map_err(|e| format!("can't write {}: {e}", log.display()))?;
        let err = out.try_clone().map_err(|e| e.to_string())?;
        let mut command = Command::new(&self.program);
        command
            .args(&self.args)
            .envs(self.env.iter().map(|(k, v)| (k, v)))
            .stdin(Stdio::null())
            .stdout(out)
            .stderr(err);
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(CREATE_NO_WINDOW);
        }
        command
            .spawn()
            .map_err(|e| format!("can't start {}: {e}", self.program.display()))
    }
}

fn python_exe() -> &'static str {
    if cfg!(windows) {
        "python.exe"
    } else {
        "bin/python3"
    }
}

/// One line in data_dir/logs/shell.log: what the shell did to the backend, and why.
pub fn log(data_dir: &Path, message: &str) {
    use std::io::Write as _;
    let logs = data_dir.join("logs");
    let path = logs.join("shell.log");
    if fs::metadata(&path).is_ok_and(|m| m.len() > 1_000_000) {
        let _ = fs::rename(&path, logs.join("shell.previous.log"));
    }
    let _ = fs::create_dir_all(&logs);
    let seconds = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or_default();
    if let Ok(mut file) = fs::OpenOptions::new().create(true).append(true).open(&path) {
        let _ = writeln!(file, "{seconds:.3} {message}");
    }
}

pub fn agent() -> ureq::Agent {
    ureq::Agent::config_builder()
        .timeout_global(Some(Duration::from_secs(10)))
        .http_status_as_error(false)
        .build()
        .into()
}

pub fn read_runtime(data_dir: &Path) -> Option<Runtime> {
    let text = fs::read_to_string(data_dir.join(RUNTIME_FILE)).ok()?;
    serde_json::from_str(&text).ok()
}

pub fn healthy(agent: &ureq::Agent, runtime: &Runtime) -> bool {
    agent
        .get(format!("{}/desktop/health", runtime.url))
        .call()
        .is_ok_and(|r| r.status() == 200)
}

/// A backend that answers and knows this secret: ours to use.
pub fn attach(agent: &ureq::Agent, data_dir: &Path) -> Option<Runtime> {
    let runtime = read_runtime(data_dir)?;
    (healthy(agent, &runtime) && new_code(agent, &runtime).is_ok()).then_some(runtime)
}

pub fn new_code(agent: &ureq::Agent, runtime: &Runtime) -> Result<String, String> {
    #[derive(Deserialize)]
    struct Code {
        code: String,
    }
    let mut response = agent
        .post(format!("{}/desktop/codes", runtime.url))
        .header("Authorization", bearer(runtime))
        .send_empty()
        .map_err(|e| e.to_string())?;
    if response.status() != 200 {
        return Err(format!("start code refused ({})", response.status()));
    }
    let code: Code = response.body_mut().read_json().map_err(|e| e.to_string())?;
    Ok(code.code)
}

pub fn status(agent: &ureq::Agent, runtime: &Runtime) -> Result<Status, String> {
    let mut response = agent
        .get(format!("{}/desktop/api/desktop/status", runtime.url))
        .header("Authorization", bearer(runtime))
        .call()
        .map_err(|e| e.to_string())?;
    if response.status() != 200 {
        return Err(format!("status refused ({})", response.status()));
    }
    response.body_mut().read_json().map_err(|e| e.to_string())
}

pub fn set_paused(agent: &ureq::Agent, runtime: &Runtime, paused: bool) -> Result<(), String> {
    let response = agent
        .patch(format!("{}/desktop/api/settings", runtime.url))
        .header("Authorization", bearer(runtime))
        .send_json(json!({ "paused": paused }))
        .map_err(|e| e.to_string())?;
    match response.status().as_u16() {
        200 => Ok(()),
        code => Err(format!("pause refused ({code})")),
    }
}

/// Tells the backend an update is downloaded, so the window can say so.
pub fn offer_update(
    agent: &ureq::Agent,
    runtime: &Runtime,
    version: &str,
    notes: &str,
) -> Result<(), String> {
    let response = agent
        .post(format!("{}/desktop/api/desktop/update", runtime.url))
        .header("Authorization", bearer(runtime))
        .send_json(json!({ "version": version, "notes": notes }))
        .map_err(|e| e.to_string())?;
    match response.status().as_u16() {
        204 => Ok(()),
        code => Err(format!("update offer refused ({code})")),
    }
}

/// Asks the backend to stop the way Ctrl+C does: workers, router, sessions.
pub fn shutdown(agent: &ureq::Agent, runtime: &Runtime) -> bool {
    agent
        .post(format!("{}/desktop/shutdown", runtime.url))
        .header("Authorization", bearer(runtime))
        .send_empty()
        .is_ok_and(|r| r.status() == 202)
}

fn bearer(runtime: &Runtime) -> String {
    format!("Bearer {}", runtime.secret)
}

/// Kills a process and everything it started. The last resort.
pub fn kill_tree(pid: u32) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        let _ = Command::new("taskkill")
            .args(["/PID", &pid.to_string(), "/T", "/F"])
            .creation_flags(CREATE_NO_WINDOW)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    }
    #[cfg(not(windows))]
    {
        // The backend kills its own workers' process groups; this is for the backend.
        let _ = Command::new("kill").args(["-9", &pid.to_string()]).status();
    }
}

/// A backend this shell started (with its process) or found running.
pub struct Backend {
    pub runtime: Runtime,
    pub child: Option<Child>,
}

impl Backend {
    /// Starts a backend, or attaches to one that's already running for this
    /// data folder (after the shell itself crashed or was restarted).
    pub fn start(
        launcher: &Launcher,
        agent: &ureq::Agent,
        stop: &dyn Fn() -> bool,
    ) -> Result<Backend, String> {
        if let Some(runtime) = attach(agent, &launcher.data_dir) {
            return Ok(Backend {
                runtime,
                child: None,
            });
        }
        // Nothing answered for desktop.json: it's left over from a backend that
        // crashed (or hangs). From here on, a desktop.json that answers is our
        // child's: the data folder's lock lets only one backend run. (Its pid
        // may not be the child's: a venv's python.exe starts the real one.)
        let stale = read_runtime(&launcher.data_dir);
        let _ = fs::remove_file(launcher.data_dir.join(RUNTIME_FILE));
        let mut retried = false;
        loop {
            let mut child = launcher.spawn()?;
            let deadline = Instant::now() + START_TIMEOUT;
            loop {
                if stop() {
                    kill_tree(child.id());
                    let _ = child.wait();
                    return Err("stopped while starting".into());
                }
                if let Ok(Some(exit)) = child.try_wait() {
                    if exit.code() == Some(ALREADY_RUNNING) && !retried {
                        // The lock is held, but nothing answered: a backend that
                        // hangs. Stop it and try once more.
                        retried = true;
                        if let Some(stale) = &stale {
                            kill_tree(stale.pid);
                        }
                        thread::sleep(Duration::from_secs(1));
                        break;
                    }
                    return Err(format!("the backend exited ({exit}) while starting"));
                }
                if let Some(runtime) = read_runtime(&launcher.data_dir) {
                    if healthy(agent, &runtime) {
                        return Ok(Backend {
                            runtime,
                            child: Some(child),
                        });
                    }
                }
                if Instant::now() > deadline {
                    kill_tree(child.id());
                    return Err("the backend didn't start in time".into());
                }
                thread::sleep(Duration::from_millis(250));
            }
        }
    }

    /// Still running? For a started backend, its process; for an attached one,
    /// whether it answers.
    pub fn alive(&mut self, agent: &ureq::Agent) -> bool {
        match &mut self.child {
            Some(child) => matches!(child.try_wait(), Ok(None)),
            None => healthy(agent, &self.runtime),
        }
    }

    /// Stops it cleanly, or kills it if it doesn't stop within `grace`.
    pub fn stop(mut self, agent: &ureq::Agent, data_dir: &Path, grace: Duration) {
        let asked = shutdown(agent, &self.runtime);
        log(
            data_dir,
            &format!(
                "asked the backend (pid {}) to stop: {asked}",
                self.runtime.pid
            ),
        );
        let deadline = Instant::now() + grace;
        while asked && Instant::now() < deadline {
            let done = match &mut self.child {
                Some(child) => !matches!(child.try_wait(), Ok(None)),
                // Not ours to wait on: it removes desktop.json last, on the way out.
                None => read_runtime(data_dir).as_ref() != Some(&self.runtime),
            };
            if done {
                if let Some(child) = &mut self.child {
                    let _ = child.wait();
                }
                log(data_dir, "the backend stopped");
                return;
            }
            thread::sleep(Duration::from_millis(200));
        }
        log(data_dir, "the backend didn't stop in time; killing it");
        kill_tree(self.child.as_ref().map_or(self.runtime.pid, Child::id));
        if let Some(child) = &mut self.child {
            let _ = child.wait();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_installed_layout_runs_embedded_python_with_bundled_git() {
        let resources = std::env::temp_dir().join(format!("rk-shell-test-{}", std::process::id()));
        let python = resources.join("python").join(python_exe());
        let git = resources.join("mingit").join("cmd");
        for dir in [
            python.parent().unwrap(),
            &git,
            &resources.join("web"),
            &resources.join("notices"),
        ] {
            fs::create_dir_all(dir).unwrap();
        }
        fs::write(&python, "").unwrap();
        fs::write(git.join("git.exe"), "").unwrap();
        fs::write(resources.join("web").join("index.html"), "").unwrap();

        let data = resources.join("data");
        let launcher = Launcher::find(Some(&resources), &data).unwrap();
        assert_eq!(launcher.program, python);
        let args: Vec<String> = launcher
            .args
            .iter()
            .map(|a| a.to_string_lossy().into())
            .collect();
        assert_eq!(args[..3], ["-m", "ringkeeper", "desktop"]);
        assert!(args.contains(&data.to_string_lossy().into_owned()));
        assert!(args.contains(&"--web-dir".to_string()));
        let env: std::collections::HashMap<_, _> = launcher.env.into_iter().collect();
        assert_eq!(env["RINGKEEPER_ENV_FILE"], "");
        assert_eq!(
            env["RINGKEEPER_NOTICES_DIR"],
            resources.join("notices").to_string_lossy()
        );
        if std::env::var_os("RINGKEEPER_WORKER_TOOLS_PATH").is_none() {
            let tools: Vec<String> =
                serde_json::from_str(&env["RINGKEEPER_WORKER_TOOLS_PATH"]).unwrap();
            assert_eq!(tools, [git.to_string_lossy()]);
        }
        fs::remove_dir_all(&resources).unwrap();
    }
}
