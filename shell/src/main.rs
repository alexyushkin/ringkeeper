//! Ringkeeper's desktop app: one window, a tray icon, and the backend it runs.
//!
//! The backend (`ringkeeper desktop`, Python) does the work and serves the web
//! app on 127.0.0.1; this shell starts it, restarts it if it crashes, opens
//! the window with a one-time code, and keeps running in the tray when the
//! window is closed. Quit (tray menu, or `ringkeeper --quit`) stops the
//! backend the same way Ctrl+C does.
//!
//! Arguments: `--hidden` starts in the tray only (used when starting at
//! sign-in); `--quit` asks the running app to quit.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;
mod logic;

use std::collections::HashSet;
use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError, Sender};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::image::Image;
use tauri::ipc::CapabilityBuilder;
use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::webview::NewWindowResponse;
use tauri::{
    AppHandle, Manager, RunEvent, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder, WindowEvent,
};
use tauri_plugin_autostart::ManagerExt as _;
use tauri_plugin_notification::NotificationExt as _;
use tauri_plugin_opener::OpenerExt as _;

use backend::{Backend, Launcher};
use logic::{Crashes, Runtime, Status};

const WINDOW: &str = "main";
const TRAY: &str = "tray";
const HIDDEN_ARG: &str = "--hidden";
const QUIT_ARG: &str = "--quit";
const STATUS_EVERY: Duration = Duration::from_secs(3);
/// How long Quit waits for running work to stop before killing the backend.
const QUIT_GRACE: Duration = Duration::from_secs(60);
const TRAY_NOTICE: &str = "Ringkeeper is still running in the tray. Right-click the icon to quit.";

const TRAY_ICON: &[u8] = include_bytes!("../icons/tray.png");
const TRAY_WAITING_ICON: &[u8] = include_bytes!("../icons/tray-waiting.png");
const OVERLAY_WAITING_ICON: &[u8] = include_bytes!("../icons/overlay-waiting.png");

enum Command {
    Retry,
    Quit,
}

/// The shell's state, shared between the main thread and the supervisor.
struct Shell {
    data_dir: PathBuf,
    agent: ureq::Agent,
    /// The running backend's address and secret, once it answers.
    runtime: Mutex<Option<Runtime>>,
    /// The secret of the backend the window was last opened for: a restarted
    /// backend has a new one, and the window needs a new code.
    window_secret: Mutex<Option<String>>,
    status: Mutex<Option<Status>>,
    seen: Mutex<HashSet<String>>,
    starting: AtomicBool,
    quitting: AtomicBool,
    commands: Mutex<Sender<Command>>,
    pause_item: Mutex<Option<CheckMenuItem<tauri::Wry>>>,
}

/// Small things the shell remembers between launches (data_dir/shell.json).
#[derive(Default, serde::Serialize, serde::Deserialize)]
struct Remembered {
    #[serde(default)]
    tray_notice_shown: bool,
}

impl Shell {
    fn remembered(&self) -> Remembered {
        fs::read_to_string(self.data_dir.join("shell.json"))
            .ok()
            .and_then(|text| serde_json::from_str(&text).ok())
            .unwrap_or_default()
    }

    fn remember(&self, remembered: &Remembered) {
        if let Ok(text) = serde_json::to_string(remembered) {
            let _ = fs::create_dir_all(&self.data_dir);
            let _ = fs::write(self.data_dir.join("shell.json"), text);
        }
    }

    fn send(&self, command: Command) {
        if let Ok(sender) = self.commands.lock() {
            let _ = sender.send(command);
        }
    }

    fn runtime(&self) -> Option<Runtime> {
        self.runtime.lock().ok().and_then(|r| r.clone())
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let hidden = args.iter().any(|a| a == HIDDEN_ARG);
    let (sender, receiver) = mpsc::channel();

    let app = tauri::Builder::default()
        // First: a second launch hands its arguments to this one and exits.
        .plugin(tauri_plugin_single_instance::init(|app, args, _cwd| {
            if args.iter().any(|a| a == QUIT_ARG) {
                quit(app);
            } else if !args.iter().any(|a| a == HIDDEN_ARG) {
                open_window(app, None);
            }
        }))
        .plugin(
            tauri_plugin_autostart::Builder::new()
                .args([HIDDEN_ARG])
                .app_name("Ringkeeper")
                .build(),
        )
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![retry])
        .setup(move |app| {
            if args.iter().any(|a| a == QUIT_ARG) {
                // Nothing was running to ask (or this is it): just leave.
                app.handle().exit(0);
                return Ok(());
            }
            let shell = Shell {
                data_dir: logic::data_dir(),
                agent: backend::agent(),
                runtime: Mutex::new(None),
                window_secret: Mutex::new(None),
                status: Mutex::new(None),
                seen: Mutex::new(HashSet::new()),
                starting: AtomicBool::new(true),
                quitting: AtomicBool::new(false),
                commands: Mutex::new(sender),
                pause_item: Mutex::new(None),
            };
            app.manage(shell);
            build_window(app.handle(), !hidden)?;
            build_tray(app.handle())?;
            let handle = app.handle().clone();
            app.notification()
                .on_action(move |_| open_window(&handle, Some("/approvals")))?;
            let handle = app.handle().clone();
            thread::Builder::new()
                .name("supervisor".into())
                .spawn(move || supervise(&handle, &receiver, !hidden))?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("couldn't start Ringkeeper's window");

    app.run(|app, event| {
        // The window only hides, but keep running even if it's gone: the tray
        // is how to quit, and Quit exits with a code.
        if let RunEvent::ExitRequested {
            api, code: None, ..
        } = event
        {
            if !app.state::<Shell>().quitting.load(Ordering::SeqCst) {
                api.prevent_exit();
            }
        }
    });
}

#[tauri::command]
fn retry(app: AppHandle) {
    app.state::<Shell>().send(Command::Retry);
}

// --- The window ---

fn build_window(app: &AppHandle, visible: bool) -> tauri::Result<()> {
    let handle = app.clone();
    let opener = app.clone();
    let window =
        WebviewWindowBuilder::new(app, WINDOW, WebviewUrl::App("index.html#starting".into()))
            .title("Ringkeeper")
            .inner_size(1200.0, 820.0)
            .min_inner_size(900.0, 600.0)
            .center()
            .visible(visible)
            // Ringkeeper's own pages stay in the window; links elsewhere open in the browser.
            .on_navigation(move |url| {
                let ours = is_ours(&handle, url);
                if !ours {
                    open_outside(&handle, url);
                }
                ours
            })
            .on_new_window(move |url, _features| {
                open_outside(&opener, &url);
                NewWindowResponse::Deny
            })
            .build()?;
    let handle = app.clone();
    window.on_window_event(move |event| {
        if let WindowEvent::CloseRequested { api, .. } = event {
            let shell = handle.state::<Shell>();
            if shell.quitting.load(Ordering::SeqCst) {
                return;
            }
            api.prevent_close();
            if let Some(window) = handle.get_webview_window(WINDOW) {
                let _ = window.hide();
            }
            let mut remembered = shell.remembered();
            if !remembered.tray_notice_shown {
                notify(&handle, "Ringkeeper", TRAY_NOTICE);
                remembered.tray_notice_shown = true;
                shell.remember(&remembered);
            }
        }
    });
    Ok(())
}

fn is_ours(app: &AppHandle, url: &Url) -> bool {
    let local = matches!(url.scheme(), "tauri") || url.host_str() == Some("tauri.localhost");
    let backend = app
        .state::<Shell>()
        .runtime()
        .and_then(|r| Url::parse(&r.url).ok())
        .is_some_and(|b| b.origin() == url.origin());
    local || backend || url.scheme() == "about"
}

fn open_outside(app: &AppHandle, url: &Url) {
    if matches!(url.scheme(), "http" | "https" | "mailto") {
        let _ = app.opener().open_url(url.as_str(), None::<&str>);
    }
}

fn splash_url(state: &str) -> Url {
    let base = if cfg!(windows) {
        "http://tauri.localhost/index.html"
    } else {
        "tauri://localhost/index.html"
    };
    Url::parse(&format!("{base}#{state}")).expect("a valid splash address")
}

fn show(window: &WebviewWindow) {
    let _ = window.show();
    let _ = window.unminimize();
    let _ = window.set_focus();
}

/// Shows the window at Ringkeeper (or at `path` in it). Opens it with a fresh
/// one-time code when the backend is new to the window. Never blocks the caller.
fn open_window(app: &AppHandle, path: Option<&'static str>) {
    let app = app.clone();
    thread::spawn(move || {
        let Some(window) = app.get_webview_window(WINDOW) else {
            return;
        };
        let shell = app.state::<Shell>();
        let Some(runtime) = shell.runtime() else {
            show(&window); // the splash says it's starting, or why it isn't
            return;
        };
        let known = shell.window_secret.lock().ok().and_then(|s| s.clone());
        let on_backend = window
            .url()
            .ok()
            .zip(Url::parse(&runtime.url).ok())
            .is_some_and(|(at, backend)| at.origin() == backend.origin());
        let target = if known.as_deref() == Some(&runtime.secret) && on_backend {
            path.map(|p| format!("{}{p}", runtime.url))
        } else {
            match backend::new_code(&shell.agent, &runtime) {
                Ok(code) => {
                    if let Ok(mut secret) = shell.window_secret.lock() {
                        *secret = Some(runtime.secret.clone());
                    }
                    let next = path.map(|p| format!("&next={p}")).unwrap_or_default();
                    Some(format!("{}/desktop/start?code={code}{next}", runtime.url))
                }
                Err(_) => None,
            }
        };
        if let Some(url) = target.and_then(|t| Url::parse(&t).ok()) {
            let _ = window.navigate(url);
        }
        show(&window);
    });
}

/// The app's pages come from the backend, a remote origin to Tauri, so they get
/// no access to the shell by default. They may open the folder picker (the
/// project form's Browse button), and nothing else, and only from this
/// backend's own address.
fn allow_folder_picker(app: &AppHandle, runtime: &Runtime) {
    let Ok(url) = Url::parse(&runtime.url) else {
        return;
    };
    let capability = CapabilityBuilder::new(format!("folder-picker-{}", url.port().unwrap_or(0)))
        .remote(format!("{}/*", url.origin().ascii_serialization()))
        .local(false)
        .window(WINDOW)
        .permission("dialog:allow-open");
    if let Err(error) = app.add_capability(capability) {
        let shell = app.state::<Shell>();
        backend::log(&shell.data_dir, &format!("no folder picker: {error}"));
    }
}

fn show_splash(app: &AppHandle, state: &str) {
    if let Some(window) = app.get_webview_window(WINDOW) {
        let _ = window.navigate(splash_url(state));
    }
    if let Ok(mut secret) = app.state::<Shell>().window_secret.lock() {
        *secret = None;
    }
}

fn notify(app: &AppHandle, title: &str, body: &str) {
    let _ = app.notification().builder().title(title).body(body).show();
}

// --- The tray ---

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Open Ringkeeper", true, None::<&str>)?;
    let pause = CheckMenuItem::with_id(
        app,
        "pause",
        "Pause all routines",
        false,
        false,
        None::<&str>,
    )?;
    let quit_item = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let menu = Menu::with_items(
        app,
        &[
            &open,
            &pause,
            &PredefinedMenuItem::separator(app)?,
            &quit_item,
        ],
    )?;
    if let Ok(mut item) = app.state::<Shell>().pause_item.lock() {
        *item = Some(pause);
    }
    TrayIconBuilder::with_id(TRAY)
        .icon(Image::from_bytes(TRAY_ICON)?)
        .tooltip("Ringkeeper: starting…")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "open" => open_window(app, None),
            "pause" => toggle_pause(app),
            "quit" => quit(app),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                open_window(tray.app_handle(), None);
            }
        })
        .build(app)?;
    Ok(())
}

fn toggle_pause(app: &AppHandle) {
    let app = app.clone();
    thread::spawn(move || {
        let shell = app.state::<Shell>();
        let wanted = shell
            .pause_item
            .lock()
            .ok()
            .and_then(|item| item.as_ref().and_then(|i| i.is_checked().ok()))
            .unwrap_or(false);
        if let Some(runtime) = shell.runtime() {
            if backend::set_paused(&shell.agent, &runtime, wanted).is_err() {
                notify(
                    &app,
                    "Ringkeeper",
                    "Couldn't change that. Try again in a moment.",
                );
            }
            refresh_status(&app, &runtime);
        }
    });
}

fn quit(app: &AppHandle) {
    let shell = app.state::<Shell>();
    if shell.quitting.swap(true, Ordering::SeqCst) {
        return;
    }
    if let Some(tray) = app.tray_by_id(TRAY) {
        let _ = tray.set_tooltip(Some("Ringkeeper: stopping…"));
    }
    if let Some(window) = app.get_webview_window(WINDOW) {
        if window.is_visible().unwrap_or(false) {
            let _ = window.navigate(splash_url("stopping"));
        }
    }
    shell.send(Command::Quit);
}

/// Applies the backend's status to the tray, the taskbar and sign-in start.
fn apply_status(app: &AppHandle, status: &Status) {
    let shell = app.state::<Shell>();
    let waiting = !status.waiting.is_empty();
    if let Some(tray) = app.tray_by_id(TRAY) {
        let icon = if waiting {
            TRAY_WAITING_ICON
        } else {
            TRAY_ICON
        };
        if let Ok(image) = Image::from_bytes(icon) {
            let _ = tray.set_icon(Some(image));
        }
        let _ = tray.set_tooltip(Some(logic::tooltip(Some(status), false)));
    }
    if let Some(window) = app.get_webview_window(WINDOW) {
        let overlay = waiting
            .then(|| Image::from_bytes(OVERLAY_WAITING_ICON).ok())
            .flatten();
        let _ = window.set_overlay_icon(overlay);
    }
    if let Ok(item) = shell.pause_item.lock() {
        if let Some(item) = item.as_ref() {
            let _ = item.set_checked(status.paused);
        }
    }
    let notices = shell
        .seen
        .lock()
        .map(|mut seen| logic::notices(&mut seen, &status.waiting))
        .unwrap_or_default();
    for notice in notices {
        notify(app, &notice.title, &notice.body);
    }
    sync_start_at_login(app, status.start_at_login);
}

fn sync_start_at_login(app: &AppHandle, wanted: bool) {
    // A dev build would register target/debug/ringkeeper.exe: only when asked.
    if cfg!(debug_assertions) && std::env::var_os("RINGKEEPER_SHELL_AUTOSTART").is_none() {
        return;
    }
    let autostart = app.autolaunch();
    if autostart.is_enabled().ok() != Some(wanted) {
        let _ = if wanted {
            autostart.enable()
        } else {
            autostart.disable()
        };
    }
}

fn refresh_status(app: &AppHandle, runtime: &Runtime) {
    let shell = app.state::<Shell>();
    if let Ok(status) = backend::status(&shell.agent, runtime) {
        apply_status(app, &status);
        if let Ok(mut current) = shell.status.lock() {
            *current = Some(status);
        }
    }
}

fn set_starting(app: &AppHandle, starting: bool) {
    app.state::<Shell>()
        .starting
        .store(starting, Ordering::SeqCst);
    if starting {
        if let Some(tray) = app.tray_by_id(TRAY) {
            let _ = tray.set_tooltip(Some(logic::tooltip(None, true)));
        }
    }
}

// --- The supervisor: keeps one backend running until Quit ---

fn supervise(app: &AppHandle, commands: &Receiver<Command>, show_when_ready: bool) {
    let shell = app.state::<Shell>();
    let launcher = match Launcher::find(app.path().resource_dir().ok().as_deref(), &shell.data_dir)
    {
        Ok(launcher) => launcher,
        Err(error) => {
            backend::log(&shell.data_dir, &error);
            show_splash(app, "failed");
            wait_for_quit(app, commands);
            return;
        }
    };
    let mut crashes = Crashes::default();
    let mut first = true;
    loop {
        set_starting(app, true);
        let quitting = || shell.quitting.load(Ordering::SeqCst);
        let started = Backend::start(&launcher, &shell.agent, &quitting);
        let mut current = match started {
            Ok(current) => {
                let how = if current.child.is_some() {
                    "started"
                } else {
                    "found"
                };
                let pid = current.runtime.pid;
                backend::log(&shell.data_dir, &format!("{how} the backend (pid {pid})"));
                current
            }
            Err(_) if quitting() => break,
            Err(error) => {
                backend::log(
                    &shell.data_dir,
                    &format!("couldn't start the backend: {error}"),
                );
                if !wait_after_crash(app, commands, &mut crashes) {
                    break;
                }
                continue;
            }
        };
        if let Ok(mut runtime) = shell.runtime.lock() {
            *runtime = Some(current.runtime.clone());
        }
        allow_folder_picker(app, &current.runtime);
        set_starting(app, false);
        refresh_status(app, &current.runtime);
        let visible = app
            .get_webview_window(WINDOW)
            .is_some_and(|w| w.is_visible().unwrap_or(false));
        if (first && show_when_ready) || visible {
            open_window(app, None);
        }
        first = false;

        // Watch it until it stops or the user quits.
        let mut last_status = Instant::now();
        let mut strikes = 0;
        loop {
            match commands.recv_timeout(Duration::from_secs(1)) {
                Ok(Command::Quit) => {
                    backend::log(&shell.data_dir, "quitting");
                    current.stop(&shell.agent, &shell.data_dir, QUIT_GRACE);
                    app.exit(0);
                    return;
                }
                Ok(Command::Retry) | Err(RecvTimeoutError::Timeout) => {}
                Err(RecvTimeoutError::Disconnected) => return,
            }
            strikes = if current.alive(&shell.agent) {
                0
            } else {
                strikes + 1
            };
            // An attached backend gets three tries to answer; our own child is
            // simply gone once it has exited.
            if strikes >= if current.child.is_some() { 1 } else { 3 } {
                break;
            }
            if last_status.elapsed() >= STATUS_EVERY {
                refresh_status(app, &current.runtime);
                last_status = Instant::now();
            }
        }

        // It stopped without being asked to.
        backend::log(&shell.data_dir, "the backend stopped unexpectedly");
        if let Ok(mut runtime) = shell.runtime.lock() {
            *runtime = None;
        }
        if app
            .get_webview_window(WINDOW)
            .is_some_and(|w| w.is_visible().unwrap_or(false))
        {
            show_splash(app, "restarting");
        }
        if !wait_after_crash(app, commands, &mut crashes) {
            break;
        }
    }
    app.exit(0);
}

/// Waits before the next start; after too many crashes, until the user asks
/// to try again. False when the user quit meanwhile.
fn wait_after_crash(app: &AppHandle, commands: &Receiver<Command>, crashes: &mut Crashes) -> bool {
    let shell = app.state::<Shell>();
    if shell.quitting.load(Ordering::SeqCst) {
        return false;
    }
    match crashes.record(Instant::now()) {
        Some(delay) => match commands.recv_timeout(delay) {
            Ok(Command::Quit) | Err(RecvTimeoutError::Disconnected) => false,
            Ok(Command::Retry) | Err(RecvTimeoutError::Timeout) => true,
        },
        None => {
            show_splash(app, "failed");
            if let Some(tray) = app.tray_by_id(TRAY) {
                let _ = tray.set_tooltip(Some("Ringkeeper: not running"));
            }
            notify(
                app,
                "Ringkeeper couldn't start",
                "Open Ringkeeper to try again.",
            );
            match commands.recv() {
                Ok(Command::Retry) => {
                    crashes.clear();
                    if let Some(window) = app.get_webview_window(WINDOW) {
                        let _ = window.navigate(splash_url("starting"));
                    }
                    true
                }
                Ok(Command::Quit) | Err(_) => false,
            }
        }
    }
}

fn wait_for_quit(app: &AppHandle, commands: &Receiver<Command>) {
    while let Ok(command) = commands.recv() {
        if matches!(command, Command::Quit) {
            break;
        }
    }
    app.exit(0);
}
