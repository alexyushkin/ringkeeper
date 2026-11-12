/**
 * Every section of Settings, for the Ctrl+K search: its tab, its id on the page, its title
 * and a few plain words people search for. `/settings#tab/id` opens the tab, opens the
 * section and scrolls to it; `/settings#tab/id/run` also asks its Run question.
 *
 * `where`: "desktop" for sections only the desktop app shows, "server" for ones only a
 * server install shows; left out, both.
 */

export interface SettingsSection {
  tab: string;
  id: string;
  title: string;
  words: string;
  where?: "desktop" | "server";
}

export const SETTINGS_SECTIONS: SettingsSection[] = [
  // Claude
  { tab: "claude", id: "claude", title: "Claude", words: "sign in api key subscription login token account" },
  { tab: "claude", id: "claude-place", title: "Where Claude runs", words: "anthropic bedrock vertex cloud provider" },
  // Models
  { tab: "models", id: "models", title: "Models and effort", words: "model opus sonnet haiku effort thinking router" },
  // Desktop-only tabs
  { tab: "voice", id: "voice", title: "Voice", words: "speech microphone dictation speak language transcribe", where: "desktop" },
  { tab: "scans", id: "scans", title: "Reading scans", words: "ocr scanned pdf images text recognition", where: "desktop" },
  { tab: "developer-apps", id: "developer-apps", title: "Developer apps", words: "client id secret oauth register app sign in", where: "desktop" },
  { tab: "passes", id: "passes", title: "Passes", words: "other ai apps claude desktop cursor chatgpt mcp ringkeeper_ask", where: "desktop" },
  // Notifications
  { tab: "notifications", id: "digest", title: "Morning digest", words: "daily summary morning email report notifications" },
  { tab: "notifications", id: "health", title: "Connection check", words: "health daily test connections expiring keys notifications" },
  { tab: "notifications", id: "telegram", title: "Telegram", words: "phone bot chat approvals notifications messages" },
  { tab: "notifications", id: "slack", title: "Slack", words: "chat channel approvals notifications messages" },
  { tab: "notifications", id: "discord", title: "Discord", words: "chat channel approvals notifications messages" },
  // Security
  { tab: "security", id: "docker-tasks", title: "Run tasks in Docker", words: "security isolation container sandbox", where: "desktop" },
  // Advanced
  { tab: "advanced", id: "work", title: "Work", words: "tasks at once concurrent transcripts retention max steps turns" },
  { tab: "advanced", id: "work", title: "Spending cap per task", words: "budget cost money dollars limit spend" },
  { tab: "advanced", id: "this-computer", title: "This computer", words: "start at login startup launch", where: "desktop" },
  { tab: "advanced", id: "hotkey", title: "Quick request", words: "hotkey shortcut keyboard key box", where: "desktop" },
  { tab: "advanced", id: "claude-desktop", title: "From Claude Desktop", words: "import tools mcp servers", where: "desktop" },
  { tab: "advanced", id: "setup-check", title: "Check my setup", words: "setup test check connections diagnose report probe" },
  { tab: "advanced", id: "self-test", title: "Self-test", words: "test scenarios check report", where: "desktop" },
  { tab: "advanced", id: "passkeys", title: "Passkeys", words: "security login sign in webauthn", where: "server" },
  { tab: "advanced", id: "recovery-key", title: "Recovery key", words: "security vault secrets backup download", where: "server" },
  // Memory
  { tab: "memory", id: "every-project", title: "Notion for every project", words: "memory notes notion projects" },
  { tab: "memory", id: "my-memory", title: "My memory", words: "memory notes notion personal mine" },
  { tab: "memory", id: "notion-memory", title: "Notion AI memory", words: "memory notion ai business" },
  { tab: "memory", id: "shared-notes", title: "Shared notes", words: "memory notes notion team shared" },
  // Backup
  { tab: "backup", id: "backup-make", title: "Make a backup", words: "backup save copy export", where: "desktop" },
  { tab: "backup", id: "backup-restore", title: "Restore a backup", words: "backup restore import recover", where: "desktop" },
  { tab: "backup", id: "backup-auto", title: "Automatic backups", words: "backup automatic schedule daily", where: "desktop" },
  // General, Appearance, About
  { tab: "general", id: "send-with", title: "Send with", words: "enter ctrl+enter keyboard send message" },
  { tab: "appearance", id: "appearance", title: "Appearance", words: "theme dark light mode colors" },
  { tab: "about", id: "about", title: "About and updates", words: "version update check for updates licenses" },
];

/** The sections this install shows. */
export function shownSections(desktop: boolean): SettingsSection[] {
  return SETTINGS_SECTIONS.filter((s) => !s.where || s.where === (desktop ? "desktop" : "server"));
}

/** Where a section is: its tab, and the section on it. `run` also asks its Run question. */
export function sectionHref(section: Pick<SettingsSection, "tab" | "id">, run = false): string {
  return `/settings#${section.tab}/${section.id}${run ? "/run" : ""}`;
}

/** What the URL's hash names: "#advanced/setup-check/run" → the section, and whether to run. */
export function namedInHash(hash: string): { tab: string; section: string; run: boolean } {
  const [tab = "", section = "", run = ""] = hash.replace(/^#/, "").split("/");
  return { tab, section, run: run === "run" };
}
