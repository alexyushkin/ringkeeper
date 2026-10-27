//! The shell's decisions that don't need a window: what the backend told us,
//! what to notify about, what the tray says, and when to stop restarting.

use std::collections::{HashSet, VecDeque};
use std::path::PathBuf;
use std::time::{Duration, Instant};

use serde::Deserialize;

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
    pub start_at_login: bool,
    pub waiting: Vec<Waiting>,
    /// Tasks running or about to: an update waits for these.
    #[serde(default)]
    pub running: u32,
    /// The user asked to restart into the downloaded update.
    #[serde(default)]
    pub install_update: bool,
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

/// Check (and download) now? When it's time and no work is running: never mid-task.
pub fn check_for_update_now(status: Option<&Status>, now: Instant, next: Instant) -> bool {
    now >= next && status.is_some_and(|s| s.running == 0)
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
            start_at_login: true,
            waiting: vec![],
            running: 0,
            install_update: false,
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
        assert!(!check_for_update_now(Some(&status), start, start));
        assert!(!check_for_update_now(None, start, start)); // backend not up yet
        status.running = 0;
        assert!(check_for_update_now(Some(&status), start, start));
        assert!(!check_for_update_now(
            Some(&status),
            start,
            start + UPDATE_EVERY
        ));

        assert!(!install_update_now(&status, true)); // the user didn't ask
        status.install_update = true;
        assert!(install_update_now(&status, true));
        assert!(!install_update_now(&status, false));
        status.running = 2;
        assert!(!install_update_now(&status, true)); // waits for the work to finish
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
