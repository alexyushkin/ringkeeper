//! The shell's decisions that don't need a window: what the backend told us,
//! what to notify about, what the tray says, and when to stop restarting.

use std::collections::{HashSet, VecDeque};
use std::path::PathBuf;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};

/// `desktop.json`, written by the backend once it listens (see desktop/server.py).
#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
pub struct Runtime {
    pub url: String,
    pub secret: String,
    pub pid: u32,
}

/// One decision waiting for the user (GET /desktop/status).
#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
pub struct Waiting {
    pub id: String,
    pub kind: String,
    pub project: Option<String>,
    pub summary: String,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
pub struct Status {
    pub paused: bool,
    /// The tray's "Pause all routines".
    #[serde(default)]
    pub routines_paused: bool,
    pub start_at_login: bool,
    pub waiting: Vec<Waiting>,
    /// Tasks running or about to: an update waits for these.
    #[serde(default)]
    pub running: u32,
    /// The user asked to restart into the downloaded update.
    #[serde(default)]
    pub install_update: bool,
    /// "Check automatically" (Settings > About): on unless the user turned it off.
    #[serde(default = "yes")]
    pub check_updates: bool,
    /// A check the user asked for in the window (its id), until the shell reports it.
    #[serde(default)]
    pub check_update: Option<String>,
    /// A page an Open link from Slack or Discord asked for (its id and path).
    #[serde(default)]
    pub open: Option<OpenRequest>,
    /// The quick request box's key ("Super+Shift+R"); None: it's off.
    #[serde(default)]
    pub hotkey: Option<String>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
pub struct OpenRequest {
    pub id: String,
    pub path: String,
}

/// A path as a query value (`next=`): all but unreserved characters escaped.
pub fn encode(text: &str) -> String {
    text.bytes()
        .map(|b| match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'.' | b'_' | b'~' | b'/' => {
                char::from(b).to_string()
            }
            _ => format!("%{b:02X}"),
        })
        .collect()
}

/// A page to open that this shell hasn't opened yet: one of the app's own pages.
pub fn asked_to_open(status: &Status, handled: Option<&str>) -> Option<String> {
    status
        .open
        .as_ref()
        .filter(|open| Some(open.id.as_str()) != handled)
        .filter(|open| open.path.starts_with('/') && !open.path.starts_with("//"))
        .filter(|open| !open.path.contains(['\\', ':']))
        .map(|open| open.path.clone())
}

/// When another app already holds the key.
pub fn hotkey_taken(keys: &str) -> String {
    format!("{keys} is taken by another app. Choose another key in Settings › Advanced › Quick request.")
}

fn yes() -> bool {
    true
}

#[derive(Debug, PartialEq, Eq)]
pub struct Notice {
    pub title: String,
    pub body: String,
}

/// More new decisions than this at once get one notification, not one each.
const NOTICES_AT_ONCE: usize = 3;

/// Updates: checked at start and then every 6 hours, sooner again after a failed check.
pub const UPDATE_EVERY: Duration = Duration::from_secs(6 * 3600);
pub const UPDATE_RETRY: Duration = Duration::from_secs(3600);

/// Check (and download) now? When the user asked; otherwise, checking
/// automatically, when it's time and no work is running. Never installs: that
/// waits for the user, and for running work to finish.
pub fn check_for_update_now(
    status: Option<&Status>,
    asked: bool,
    now: Instant,
    next: Instant,
) -> bool {
    asked || (now >= next && status.is_some_and(|s| s.check_updates && s.running == 0))
}

/// A check asked for in the window that this shell hasn't handled yet.
pub fn asked_in_window(status: Option<&Status>, handled: Option<&str>) -> Option<String> {
    status
        .and_then(|s| s.check_update.clone())
        .filter(|id| Some(id.as_str()) != handled)
}

/// What the shell tells the backend about a check (POST /desktop/update/checked):
/// started, downloading, or ended. The last one that ended is remembered.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct CheckReport {
    /// checking, downloading, current, ready or failed
    pub state: String,
    /// The window's request this answers, if it asked.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub request: Option<String>,
    #[serde(default)]
    pub version: Option<String>,
    #[serde(default)]
    pub error: Option<String>,
    /// When it ended (unix seconds).
    #[serde(default)]
    pub at: Option<f64>,
}

impl CheckReport {
    pub fn new(state: &str) -> Self {
        Self {
            state: state.into(),
            request: None,
            version: None,
            error: None,
            at: None,
        }
    }
}

/// Why a check failed, sorted into what the user can do about it.
#[derive(Debug, PartialEq, Eq)]
pub enum UpdateFailure {
    Offline,
    Signature,
    Unreadable,
    Other(String),
}

/// What the window says when a check fails.
pub fn failure_words(failure: &UpdateFailure) -> String {
    match failure {
        UpdateFailure::Offline => {
            "Couldn't reach the update server. Check that this computer is online, then try again."
                .into()
        }
        UpdateFailure::Signature => "The update's signature couldn't be verified, so it wasn't installed. This version keeps running.".into(),
        UpdateFailure::Unreadable => {
            "The update server's answer couldn't be read. Try again later.".into()
        }
        // The details go to the log (main.rs), not to the window.
        UpdateFailure::Other(_) => "Ringkeeper couldn't check for updates. Try again later from Settings › About.".into(),
    }
}

/// Install now? Only when the user asked, the download is here, and nothing runs.
pub fn install_update_now(status: &Status, downloaded: bool) -> bool {
    status.install_update && downloaded && status.running == 0
}

/// Where the backend keeps its data. Must match `ringkeeper.desktop.paths.data_dir`.
pub fn data_dir() -> PathBuf {
    if let Some(dir) = std::env::var_os("RINGKEEPER_SHELL_DATA_DIR") {
        return PathBuf::from(dir); // tests and side-by-side dev copies
    }
    let base = dirs::data_local_dir().unwrap_or_else(|| PathBuf::from("."));
    if cfg!(any(windows, target_os = "macos")) {
        base.join("Ringkeeper")
    } else {
        base.join("ringkeeper")
    }
}

fn title(kind: &str) -> &'static str {
    match kind {
        "approve" => "Approval waiting",
        "plan" => "A plan is waiting for you",
        "question" => "A question is waiting for you",
        "route" => "A request needs a project",
        _ => "Something is waiting for you",
    }
}

/// Notifications for the decisions not seen before. `seen` keeps every id this
/// shell has looked at, so nothing is announced twice (also across backend restarts).
pub fn notices(seen: &mut HashSet<String>, waiting: &[Waiting]) -> Vec<Notice> {
    let new: Vec<&Waiting> = waiting
        .iter()
        .filter(|w| seen.insert(w.id.clone()))
        .collect();
    if new.len() > NOTICES_AT_ONCE {
        return vec![Notice {
            title: format!("{} things are waiting for you", new.len()),
            body: "Open Ringkeeper to see them.".into(),
        }];
    }
    new.into_iter()
        .map(|w| Notice {
            title: title(&w.kind).into(),
            body: match &w.project {
                Some(project) => format!("{project}: {}", w.summary),
                None => w.summary.clone(),
            },
        })
        .collect()
}

/// What the tray icon says on hover.
pub fn tooltip(status: Option<&Status>, starting: bool) -> String {
    match status {
        _ if starting => "Ringkeeper: starting…".into(),
        None => "Ringkeeper".into(),
        Some(s) => {
            let mut parts = Vec::new();
            match s.waiting.len() {
                0 => {}
                1 => parts.push("1 thing waiting for you".to_string()),
                n => parts.push(format!("{n} things waiting for you")),
            }
            if s.paused {
                parts.push("paused".into());
            }
            if parts.is_empty() {
                "Ringkeeper".into()
            } else {
                format!("Ringkeeper: {}", parts.join(", "))
            }
        }
    }
}

/// Restarts after crashes: quickly at first, then slower, and not forever.
#[derive(Default)]
pub struct Crashes {
    recent: VecDeque<Instant>,
}

/// Five crashes within this window means something is wrong that a restart won't fix.
const CRASH_WINDOW: Duration = Duration::from_secs(120);
const CRASH_LIMIT: usize = 5;

impl Crashes {
    /// Records a crash. Returns how long to wait before starting again, or None
    /// to give up until the user asks to try again.
    pub fn record(&mut self, now: Instant) -> Option<Duration> {
        while self
            .recent
            .front()
            .is_some_and(|t| now.duration_since(*t) > CRASH_WINDOW)
        {
            self.recent.pop_front();
        }
        self.recent.push_back(now);
        if self.recent.len() >= CRASH_LIMIT {
            return None;
        }
        Some(Duration::from_secs(1 << (self.recent.len() - 1))) // 1, 2, 4, 8 seconds
    }

    pub fn clear(&mut self) {
        self.recent.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn waiting(id: &str, kind: &str, project: Option<&str>) -> Waiting {
        Waiting {
            id: id.into(),
            kind: kind.into(),
            project: project.map(Into::into),
            summary: format!("summary {id}"),
        }
    }

    #[test]
    fn reads_the_runtime_file_and_the_status() {
        let runtime: Runtime =
            serde_json::from_str(r#"{"url": "http://127.0.0.1:5000", "secret": "s", "pid": 42}"#)
                .unwrap();
        assert_eq!(runtime.pid, 42);
        let status: Status = serde_json::from_str(
            r#"{"paused": false, "start_at_login": true, "waiting": [
                {"id": "a", "kind": "approve", "project": null, "summary": "Run it?"}]}"#,
        )
        .unwrap();
        assert_eq!(status.waiting[0].project, None);
    }

    #[test]
    fn announces_each_decision_once() {
        let mut seen = HashSet::new();
        let first = notices(&mut seen, &[waiting("a", "approve", Some("acme-web"))]);
        assert_eq!(
            first,
            vec![Notice {
                title: "Approval waiting".into(),
                body: "acme-web: summary a".into()
            }]
        );
        let again = notices(
            &mut seen,
            &[
                waiting("a", "approve", Some("acme-web")),
                waiting("b", "question", None),
            ],
        );
        assert_eq!(again.len(), 1);
        assert_eq!(again[0].title, "A question is waiting for you");
        assert_eq!(again[0].body, "summary b");
    }

    #[test]
    fn many_at_once_make_one_notification() {
        let mut seen = HashSet::new();
        let many: Vec<Waiting> = (0..5)
            .map(|i| waiting(&i.to_string(), "plan", None))
            .collect();
        let shown = notices(&mut seen, &many);
        assert_eq!(shown.len(), 1);
        assert_eq!(shown[0].title, "5 things are waiting for you");
        assert!(notices(&mut seen, &many).is_empty());
    }

    #[test]
    fn the_tooltip_says_what_is_going_on() {
        let mut status = Status {
            paused: false,
            routines_paused: false,
            start_at_login: true,
            waiting: vec![],
            running: 0,
            install_update: false,
            check_updates: true,
            check_update: None,
            open: None,
            hotkey: None,
        };
        assert_eq!(tooltip(Some(&status), false), "Ringkeeper");
        assert_eq!(tooltip(Some(&status), true), "Ringkeeper: starting…");
        status.waiting = vec![waiting("a", "approve", None), waiting("b", "plan", None)];
        status.paused = true;
        assert_eq!(
            tooltip(Some(&status), false),
            "Ringkeeper: 2 things waiting for you, paused"
        );
    }

    #[test]
    fn updates_never_happen_mid_task() {
        let start = Instant::now();
        let mut status: Status = serde_json::from_str(
            r#"{"paused": false, "start_at_login": true, "waiting": [], "running": 1}"#,
        )
        .unwrap();
        assert!(status.check_updates); // on unless turned off
        assert!(!check_for_update_now(Some(&status), false, start, start));
        assert!(!check_for_update_now(None, false, start, start)); // backend not up yet
        status.running = 0;
        assert!(check_for_update_now(Some(&status), false, start, start));
        assert!(!check_for_update_now(
            Some(&status),
            false,
            start,
            start + UPDATE_EVERY
        ));
        // Asked for: checks now, even mid-task (only the install waits).
        status.running = 1;
        assert!(check_for_update_now(
            Some(&status),
            true,
            start,
            start + UPDATE_EVERY
        ));
        // "Check automatically" off: only when asked.
        status.running = 0;
        status.check_updates = false;
        assert!(!check_for_update_now(Some(&status), false, start, start));
        assert!(check_for_update_now(Some(&status), true, start, start));

        assert!(!install_update_now(&status, true)); // the user didn't ask
        status.install_update = true;
        assert!(install_update_now(&status, true));
        assert!(!install_update_now(&status, false));
        status.running = 2;
        assert!(!install_update_now(&status, true)); // waits for the work to finish
    }

    #[test]
    fn a_check_asked_for_in_the_window_is_handled_once() {
        let mut status: Status = serde_json::from_str(
            r#"{"paused": false, "start_at_login": true, "waiting": [], "check_update": "a1"}"#,
        )
        .unwrap();
        assert_eq!(asked_in_window(Some(&status), None), Some("a1".into()));
        assert_eq!(asked_in_window(Some(&status), Some("a1")), None);
        status.check_update = None;
        assert_eq!(asked_in_window(Some(&status), Some("a1")), None);
        assert_eq!(asked_in_window(None, None), None);
    }

    #[test]
    fn a_path_is_escaped_as_a_query_value() {
        assert_eq!(encode("/task?id=a1&x=y"), "/task%3Fid%3Da1%26x%3Dy");
        assert_eq!(encode("/settings#about"), "/settings%23about");
    }

    #[test]
    fn an_open_link_opens_its_page_once() {
        let mut status = Status {
            paused: false,
            routines_paused: false,
            start_at_login: false,
            waiting: vec![],
            running: 0,
            install_update: false,
            check_updates: true,
            check_update: None,
            open: None,
            hotkey: None,
        };
        assert_eq!(asked_to_open(&status, None), None);
        status.open = Some(OpenRequest {
            id: "o1".into(),
            path: "/approvals".into(),
        });
        assert_eq!(asked_to_open(&status, None), Some("/approvals".into()));
        assert_eq!(asked_to_open(&status, Some("o1")), None);
        for path in ["//evil.example", "https://evil.example", "/x\\y"] {
            status.open = Some(OpenRequest {
                id: "o2".into(),
                path: path.into(),
            });
            assert_eq!(asked_to_open(&status, None), None, "{path}");
        }
    }

    #[test]
    fn a_failed_check_says_what_to_do() {
        assert!(failure_words(&UpdateFailure::Offline).contains("online"));
        assert!(failure_words(&UpdateFailure::Signature).contains("signature couldn't be verified"));
        let other = failure_words(&UpdateFailure::Other("disk full".into()));
        assert!(other.contains("Try again later"));
        assert!(!other.contains("disk full"));
    }

    #[test]
    fn restarts_back_off_then_give_up() {
        let mut crashes = Crashes::default();
        let start = Instant::now();
        let delays: Vec<Option<Duration>> = (0..5)
            .map(|i| crashes.record(start + Duration::from_secs(i)))
            .collect();
        assert_eq!(delays[0], Some(Duration::from_secs(1)));
        assert_eq!(delays[3], Some(Duration::from_secs(8)));
        assert_eq!(delays[4], None);
        // Long after, a crash starts the count again.
        let later = start + Duration::from_secs(600);
        assert_eq!(crashes.record(later), Some(Duration::from_secs(1)));
    }
}
