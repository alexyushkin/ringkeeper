# Changelog

Each release of the desktop app has an entry here; the release workflow
publishes it as the release notes and the app shows it with the update.

## 0.1.0-beta.40 (2026-11-12)

- **Back up to Google Drive.** Settings › Backup can upload each backup, the ones you make and
  the weekly ones, into a "Ringkeeper backups" folder in your own Google Drive, keeping as many
  as you keep here. It's the same file, locked with your password, which never leaves this
  computer. Only your own Google account can hold them, never a client's, with "Drive: files
  Ringkeeper creates" (you can add it from there). The last upload and a link to it are shown;
  a failed upload is said on Home and in Settings, and the copy on this computer is kept.
  Restoring is as before: download the file from Drive, then pick it.
- **Airtable's own server can be connected.** It was in Ringkeeper's list, but search showed only
  the built-in Airtable, and typing its address said "connect it from Recommended above" with
  nothing there. Both now show, each saying what it's for: built in for bases and records with
  undo, Airtable's own server for automations and interfaces. Typing a known service's address
  opens its form right there. The built-in connection's card says what it can't see and offers
  the server for the same client and base; a project with both tells its tasks which is for
  what; a task that couldn't see automations says the server would; and Check my setup says
  whether the server's tools include automations.
- **Home's last 24 hours tells it straight.** Check my setup's requests and the demo's tasks
  aren't counted as your clients' work: a line says they ran, with a link to the report. A
  task that wasn't done is counted apart from one that broke ("1 not done" in amber, "1 failed"
  in red), here, in the morning digest and in a client's month report. Each count opens those
  tasks, and the heading says when the window started.
- **Counts of a part are right.** Asked to count a client's n8n workflows by whose they were, a task
  got both wrong: it counted a list by hand. Tasks now count with a filter and
  quote the total the tool gives, and a count next to a list must match it. n8n's workflow list
  can be filtered by words in the name, its start and tags, and a long list says how many there
  are by name prefix and by tag. GitHub's pull requests can be filtered by label too. Asked how
  many are "named" a client, a task used the exact-name filter and said 0: when no workflow has
  that exact name, the list now says how many have it in their name. The self-test has a new
  scenario for it.
- **Partly done.** A task that did part of the job and couldn't do the rest showed only "Not
  done". Its card and page now say **Partly done**, with what was done and what wasn't. It still
  counts as not done.
- **A task stops at the connection's own tools.** With no tool for the job, a task tried the
  same Airtable connection through another route. It now stops there and says so.
- **Notes say exactly where they went.** A project's note saved to its Notion database was
  reported as "Notion", and a task took that for your personal memory and asked you to delete it
  by hand. Answers, the activity log and **Saved to memory** now say "this project's notes
  (Bayside, in Notion)", "My memory" or "shared notes".
- **Better notes.** One fact per note, about what identifies this client's things (names and
  naming patterns, IDs, filters, which base or workflow does what), never counts, dates or how a
  tool works in general. A note that repeats one already there is refused as "already noted".
- **Notes in Notion read better.** A short title with the whole note in its page, a Kind column
  (Where things live, How we do it, Gotcha, Decision), Agent says Ringkeeper when no named agent
  ran, and Source task shows the request's first words with its ID. Existing databases get the
  Kind column; their rows aren't changed.

## 0.1.0-beta.39 (2026-11-12)

- **Backups that outlive this computer.** Settings › Backup offered only a local folder, and a
  backup on the same disk is lost with the computer. Now:
  - The synced folders it finds (OneDrive, Google Drive, Dropbox, iCloud Drive) are one click
    away, next to Browse, for a backup and for automatic backups. Ringkeeper only writes the
    file there; the service's own app copies it.
  - A folder on the same disk that isn't synced gets a warning, with **I back this folder up
    another way** (iDrive, Backblaze, Time Machine...) to hide it for that folder.
  - The password fields say it's needed on the new computer and can't be recovered, so keep it
    in a password manager. The automatic backups' password is stored only on this computer.
  - Home's **Turn on automatic backups** opens the folder choice first.
- **A list request gets the list.** "List the tables" came back with every field of 39 tables.
  Tasks now answer what was asked, at the size it was asked, and offer the detail in one line.
- **Answers don't talk about memory unless something was saved.** Check my setup's answers said
  things like "I also saved no memory notes" and "I didn't change any files or save any
  memory". Its tasks were still told to save your preferences at the end, though nothing is
  kept from a check. They're no longer told, and can't save, and every task is told to say
  nothing about memory unless it saved a note.
- **Nothing starts while Claude's limit is reached.** When the limit was reached at the moment
  Ringkeeper picked the next task, that task could still start, hit the limit, and queue
  another. It now stays queued until the limit resets.
- **Ctrl+K finds every setting.** It knew clients, projects and pages, and found nothing for
  "my setup". Now each section of Settings is there by its title and a few plain words
  ("setup", "test", "memory", "backup", "budget"), and choosing one opens Settings on the
  right tab with that section open. **Run Check my setup** and **Run the self-test** open
  theirs with the question asked, so nothing runs until you say so.
- **Show in folder finds the file when its name has a space.** On Windows it opened your
  Documents folder instead, for "Check my setup 2026-11-12.md" and any task file, download or
  report with a space in its path. It now asks Windows to select the file directly.
- **n8n runs are counted too.** A list of a workflow's runs says how many there are and how
  many of each status (succeeded, failed, running...), not only the 20 on the page, so "how
  many of its runs failed?" gets the real number. It counts the newest 1,000 at most, and says
  when there are more.
- **A Google mail search shows the start of each message.** Each message found now comes with
  a preview of its own text (not the thread it quotes) and whether it's unread, instead of
  Gmail's one-line snippet, so a summary of the inbox says what each email is about. It
  doesn't make a summary cheaper: most of its cost is the task's own instructions and tools,
  sent again on every step.

## 0.1.0-beta.38 (2026-11-12)

- **Notion memory is one switch now.** Linking the Notion memory connection to a project looked
  like it turned Notion memory on, and it didn't: notes stayed here unless the project's own
  Memory said Notion.
  - **Settings › Memory › Notion for every project** (also on the connection's card): pick one
    root page. Ringkeeper makes a page per client under it and a database per project under its
    client's page, so one client's page never holds another's notes. Projects already in Notion
    keep their place, and new projects follow.
  - The Notion memory connection isn't a project's tool any more: it isn't linked to projects,
    tasks can't call it, and it's left out of Check my setup and Home's requests to try. Its Test
    button still works. Existing links are removed.
  - Each project's About says where its notes go, with a link to its Notion database.
- **Tasks save what they find, and start from it.** Before, a task was told about memory only by
  its memory tool, so tasks ended with "nothing needed saving" and kept nothing.
  - Every task now starts with its project's latest notes, and before it finishes it saves what
    a later run would otherwise have to find again: where things live, your decisions and
    corrections, how the client wants things done, and gotchas. One to three short notes, never
    the result or secrets.
  - An answer mentions memory only when something was saved. **Saved to memory** shows each note
    under the result, on the task's page and in the conversation.
  - Add a note yourself: **Add a note** on a project's Memory, **Remember this** under a result
    or a reply, or say "remember that …" in a conversation. With a clear project it's saved
    straight away, with no task, and the reply says what was saved and where.
  - A new self-test scenario: a fact one task found is saved, and the next task, in a new
    conversation, uses it without searching again.
- **Check my setup checks each connection once, three at a time.** A connection linked to
  several projects gets one request, in the first of them, where it used to get one per project.
  A project whose connections were all checked elsewhere still gets a request of its own, so
  you know requests reach it. Three requests run at the same time, and each one's row shows on
  the page as soon as it's back.
- **Check my setup tests MCP servers, add-on tools and HTTP APIs without a model.** It connects
  to each one itself, as its Test button does, and says how many tools it offers. Before, it
  asked a task to "list the tools it offers", which could be reworded on the way and fail.
- **A Google account is tried on what it was given.** Check my setup and Home's requests to try
  ask a Google account given only mail about its unread mail, not its calendar, which it can't
  read. With Calendar, it's still this week's events.
- **Two connections no longer read the same.** When a client's connection gets a name another
  one already has, its client's name is added: "Google · Acme · info@acme.example". Ones that
  read the same before this update are renamed that way once; your own keep theirs. Check my
  setup and Home's requests to try name a connection by its unique name (`acme-google`), so a
  task always uses the one meant.
- **A request in Check my setup works only when its answer is complete.** An answer that says
  it's only part of what was asked, like "the list is partly read" or "a background agent is
  still fetching", no longer counts as worked; the report quotes what it said.
- **Check my setup's report says Worked, Needs you or Failed for each request.** A request that
  went to another project than the one it was written for fails. One that waits on you (a write
  held in Approvals, or a web lookup in a project whose tasks can't use the web) says Needs you,
  where it used to count as worked though it never ran.
- **Tasks give their answer once.** They check it before they reply, instead of saying "4 are
  active" and correcting it to three further down the same message.
- **Add-on tools with many tools no longer fill a task.** Listing an MCP server's tools gives each
  tool's name, what it does in a line and whether it runs straight away or waits for you, not
  every tool's full arguments: ElevenLabs' list was about 927,000 characters. A task can look
  for tools by words, and asks for one tool's arguments before calling it. A call with arguments
  the tool doesn't take comes back with what it does take, so the next try is right.
- **A task finishes its work before it answers.** A task could hand part of its work to a
  background agent and answer while that was still fetching, and the answer counted as done. A
  task's background shells and agents are now off, and it's told to finish everything before
  its final message, or end as Not done.
- **Counts you can trust.** Lists from n8n, Airtable, GitHub and databases say how many there
  are in all, not only on the page shown, so a task no longer counts by hand: the same n8n
  was once reported with 197 workflows, then 184. n8n also says how many workflows are on, and
  GitHub how many issues and pull requests are open. Airtable counts records up to 2,000 and
  says when there are more.
- **A spending cap on a Claude subscription too.** Only an API key had a per-task cap, so on a
  subscription a task could run on: one conversation reached an estimated $1.53. Now a task on a
  subscription pauses at the same cap (Settings › Advanced › Spending cap per task, $1 by
  default), counted from the estimated cost, and asks whether to go on, with a Continue button.
  A subscription still isn't billed per task: this is what the estimate says.
- **Data, ops and general tasks start with a shorter prompt.** Work on a project's code keeps
  Claude Code's own; anything else gets a short one with the same rules for working
  unattended. In the self-test, results held and the estimated cost per task fell by about a
  quarter.
- **Only documents open from a task's files.** **Open** works for PDFs, Office files, text,
  Markdown, CSV, JSON and pictures. Anything else a task wrote (a web page, a program, a
  script, a file with no type) is shown in its folder instead, so nothing runs by a click.
  Before, only a list of known program types was held back.
- **Keep a chat's results in the app if you'd rather.** Each Telegram, Slack and Discord
  channel has **Full results and files for requests from here** (Settings › Notifications,
  on by default). Turned off, a task you asked for from that chat ends there with only how it
  ended and a link, and its files aren't sent. The security page now says what a chat gets.
- **Home asks once to turn on automatic backups.** After your first real task, if automatic
  backups are off, Home shows **Turn on automatic backups** with a link to Settings › Backup.
  Check my setup and the demo don't count. **Not now** hides it for good, and so does turning
  them on.
- **Download CSV works in the desktop app.** It did nothing there, on every table. Now the
  table is saved in your Downloads folder (`table.csv`, then `table (2).csv`: nothing is
  overwritten), the button says where, and **Show in folder** shows it. In a browser it
  downloads as before.
- **Keep and share Check my setup and Self-test reports.** Under each report: **Save report**
  (a Markdown file, into Downloads in the desktop app), **Copy report**, **Save table as CSV**
  and **Show in folder**. **Earlier reports** lists the other days kept, to read one again.
- **"Read Acme's newest email" no longer asks which project** when only one of Acme's
  projects has the mail connection. A connection only one of a client's projects has
  decides it.
- **Tasks that can't finish say so in one go.** The note told tasks to call Not done "with
  why", and the tool takes a reason: each not-done task spent a refused call first.
- **Tasks keep where things are even when the name looks obvious,** such as a table found by
  listing the database, so the next task doesn't list it again.
- **Self-test:** the unreadable-attachment scenario passes when the task tried the attachment
  and ended as Not done. Reading that file always fails, and a failed call didn't count.
- **Self-test:** memory-reuse asks a question the tables lead to (who has spent the most),
  so the note a task saves isn't its answer, and it doesn't need the first task to list the
  tables: describing the ones it needs is fine.

## 0.1.0-beta.37 (2026-11-11)

- **Tasks read files better, wherever they come from.** Gmail, Google, any mailbox, Microsoft
  (attachments, and now OneDrive and SharePoint files too) and file storage all read files the
  same way:
  - The pictures on a page with text are read now, not only scanned pages: a price table pasted
    under a heading was skipped before. When a picture can't be read (no scan reader, or no
    time left), the answer says which pages.
  - A task can look at a page, or a picture, the way you see it. A table or a chart keeps its
    layout, which reading its text loses. This works without the scan reader.
  - A task can save a copy of an attachment or a file in the project's `attachments` folder to
    work on it. It's listed with the task's files, and nothing changes in the mailbox or the
    drive. Looking and saving run without asking, and both are in Activity.
  - PowerPoint decks (with speaker notes), attached emails (.eml and Outlook's .msg, and their
    own attachments) and .zip files are read, a file at a time. A file that can't be read says
    what it is and what would work instead, like "a Numbers spreadsheet: export it as .xlsx or
    PDF".
  - A long file starts with an outline (its pages, which have text and which have pictures, and
    the first two pages), then the task reads the pages it needs. One long PDF no longer fills
    a task.
- **Tasks use fewer tokens on every step.** Each connection lists only the tools it can use (a
  Google account with only mail access no longer lists YouTube, Drive or Calendar), and every
  tool's help is shorter.
- **A task that couldn't deliver what you asked for ends as Not done.** Even when it did part of
  it, or suggests ways forward ("I couldn't read the pricing, here's what you could try"), it no
  longer ends as Done. The self-test has a scenario for it.

- **Approvals where you're reading.** On a task or conversation page, what's waiting for you
  is under the latest result, in order, not above earlier work. One that arrives while the
  page is open scrolls into view, and if one is off-screen, a bar at the bottom ("1 approval
  waiting") jumps to it. The same goes for questions and plans.
- **An approval card shows what it approves.** For an email or a draft: To, Subject and the
  body, a long one cut after 12 lines with **Show all**. **Show details** is only for the raw
  arguments.
- **Saving an email draft doesn't wait for approval.** On Gmail, a Google account, Outlook
  or any mailbox, a draft reaches nobody, so it's saved and logged, and the task's result
  links it. Sending still waits. **Hold drafts for my OK too**, in a connection's Details,
  brings the old way back. A saved draft can now be undone (it's deleted), where the card
  used to say it couldn't be.
- **Choose which key sends: Enter or Ctrl+Enter.** Settings > General > Send with sets it for
  every box you type in: the request box on Home, the quick request, conversations, **Continue
  this task**, answers to questions and plans, and Help. Ctrl+Enter is the default, as before,
  and Cmd+Enter on a Mac. A line under each box says which key sends, Esc leaves the box, and
  while the @ list is open Enter picks from it. **Continue this task** and answers can now be
  sent from the keyboard too. Help's box takes more than one line now, so it follows the same
  choice (before, Enter sent it).
- **Routing a request can't hang.** Working out where a request goes stops after 90 seconds
  and starts over at once; if that's slow too, the request waits and is tried again later,
  and the conversation says so once. Before, one took more than 6 minutes. After 20 seconds,
  the conversation says "Still working out where this goes…".
- **Tasks know their project's connections from the start.** Each task is told each
  connection's name, kind, whether it's production and which is the default, so its first
  call names one. Before, in a project with several databases and no default, the first call
  was refused and tried again. When two of a kind are in different environments and the
  request doesn't say which, the task asks you instead of guessing.
- **A request to use a connection reaches it.** "List what Demo CRM can do for Demo Co" was
  sometimes answered as a question about Ringkeeper, without a task. A request that names a
  connection and asks to use or read it now always runs as a task in a project linked to it.
  The first suggestion for an MCP or vendor connection now reads "In Demo shop, use Demo CRM
  to list the tools it offers".
- **A follow-up stays in its conversation's project.** Once a conversation has a task in a
  project, a later message there that names no other project, client or connection runs in
  the same project, without asking. Before, a follow-up that needed no connection could go to
  your own client's project instead and find nothing to work on. The self-test has a scenario
  for it.
- **Check my setup.** Settings > Advanced > Check my setup (or `ringkeeper scenarios run
  --mine`) sends one read-only request per project and connection linked to it, through the
  real router, worker and gateway, then a question about your setup, a web lookup and a
  follow-up. The report says, per connection, where it went, whether it asked anything, what
  ran, what came back and what it cost, and lists connections linked to no project. It changes
  nothing: its tasks can't edit files or run commands, every write is held for you, and its
  conversations are tagged so **Archive them** clears them in one click.
- **A GitHub link can be pinned to several repositories.** On the same screen, **Add another
  repository**. Every call is checked against the list, and listing repositories shows only
  those. Your own GitHub connection linked to a project with no repository pinned now warns:
  "This project could reach every repository this token can."
- **Connections no project uses stand out.** Their card says **Not linked to any project**,
  with **Link**, and Home's **Get started** counts them ("2 connections aren't linked to any
  project") until they're linked.
- **Fixed:** a request from Slack reached the router and the task with Slack's markup in it
  ("<mailto:a@b.com|a@b.com>", "&amp;"). It's plain text now, as you wrote it: a link keeps its
  address, and Discord mentions and Telegram links read as words too.

## 0.1.0-beta.36 (2026-11-11)

- **A task's final message is its answer.** Asked to "tell" or "reply with" something, a
  task now reports it, instead of taking it as replying to an email (the router writes
  "report it in your final message").
- Naming a connection ("use demo-crm to...") decides the project: a request that also names
  the client isn't asked about. In the self-test, asking in words when it can't tell which
  project counts as asking, and a question answered by name is counted once.

- **Fixed:** after an update, the app came back hidden in the tray when it had started at
  sign-in, so it looked like it hadn't started. The window opens again now, saying "Updated
  to" the new version, with a link to what's new.

- **Fixed (found by the self-test):** replying with just a project's name ("Demo site")
  didn't answer "which project?" when another project's nickname ("demo") was part of it.
  The most specific name wins now.
- **The same request isn't started twice.** Sent again while its task is still running or
  waiting, in the same conversation and project, it replies "Already working on that" with
  a link, instead of starting the same work again.
- **Work on one of a client's projects is asked about.** "Add a file to Acme's project
  folder", with Acme having two projects, now asks which, however sure the router was.
  Work any of a client's projects would do still goes to its default project.
- **The router doesn't add limits you didn't state** to a task's instructions (like "don't
  open any email"), which could stop it doing what you asked.
- Self-test: declining to touch the other client's data passes in the "pushed to cross"
  scenarios even when the task then ends "not done"; the demo inbox lists dates; a reply
  with just a name is sent once per question, and fails plainly if it didn't answer.

## 0.1.0-beta.35 (2026-11-11)

- **Tasks that don't work on the code don't need a clean folder.** Work with data or
  connections, and general work like a web lookup, no longer uses git: it starts even when the
  folder has uncommitted changes, and it doesn't wait for a code task in the same project. If
  it does change files there, its page says so. In a code task, only the files the task itself
  edited go on its branch; a file someone else changed meanwhile stays out, and the task says
  which. See [A task's changes to files](docs/concepts.md#a-tasks-changes-to-files).

- **Fixed:** a task's result was cut at 4,000 characters, so a long table ended mid-row
  (a 105-row list of workflows stopped at row 60). The task's page now shows the whole result.
  Task cards and chat messages still show the start, with a link to the rest.

- **Fixed:** while Claude was unavailable, a waiting request added the same notice to its
  conversation on every retry, every 2 minutes. It's said once, and once more when Claude is
  back. Retries wait 2, 5 and 15 minutes, then every half hour, and stop after 12 hours with
  **Try again**.

- **The usage-limit pause can be cut short, and only Claude starts it.** Home's notice has
  **Try now**, and changing Claude's sign-in, key or place in Settings stops the wait too. Only
  Claude's own signals pause anything: a task that failed while reading about an API's "rate
  limit reached", or got "Internal Server Error" from n8n, is an ordinary failure and no longer
  pauses everything.

- **Files a task makes.** Its page and its result in the conversation list the files it made
  or changed, with **Open** and **Show in folder** in the desktop app (a program or script is
  only shown, never opened). Results in Slack, Discord and Telegram come with the files when
  they're small; a secret-looking file, like a `.env`, never goes. Tables in a result have
  **Copy** and **Download CSV**. Slack apps set up before need the manifest pasted again for
  files. See [Files a task makes](docs/concepts.md#files-a-task-makes).

- **Web access is a project setting of its own.** It no longer depends on how independently
  a project works: on the project's Settings tab, **Web access** is on for your own client's
  projects and off for a client's unless you change it. A request that needs the web where
  it's off asks once, with **Allow web for this project and retry**, in the app and in your
  chats. A project that had the web before keeps it.

- **Resume tasks a restart cut short.** After a restart or an update, Home lists the tasks
  that were running, with **Resume** for each and **Resume all**. A resumed task picks up
  where it stopped. See
  [A task stopped when Ringkeeper restarted](docs/troubleshooting.md#a-task-stopped-when-ringkeeper-restarted).

- **Fixed:** on Windows, voice transcription could lose its "too large" or "busy" answer
  and report a dropped connection instead: the service now reads and drops a refused upload
  before closing. The speech-to-text tests that need Docker skip, with a reason, when it isn't
  running.

## 0.1.0-beta.34 (2026-11-10)

- **Fixed:** a task that ran but couldn't do what was asked ("not done") was missed in places:
  the morning summary now lists it as a problem with its reason, the client's month report
  says "not done", cleanup counts it as unfinished, a Claude Desktop pass is told "not done"
  and why, and the self-test no longer tries to stop it.

- **Fixed:** a short new request that named a project ("List Acme's active workflows") could
  be taken as the answer to an older "which project?" question, and the request was lost. Only
  a reply that's just a name, sent right after the question, answers it now; "yes" counts as a
  go-ahead only right after one. And a name sent after a greeting no longer re-routes the
  greeting: only a request the router said it couldn't place is placed by a name.

- **Tasks leave your folder as they found it.** One task works in a project's folder at a
  time (another waits, and says so). When a task ends, the folder is checked out as it was;
  a task that changed no files leaves no branch, and the task page shows none. Uncommitted
  changes are never carried onto a task's branch: the task doesn't start and says why. On a
  task's Changes tab, **Keep these changes** merges its branch into main (refused plainly on a
  conflict) and **Discard** deletes it, and a follow-up in the same conversation keeps working
  on the same branch. See [A task's changes to files](docs/concepts.md#a-tasks-changes-to-files).

- **Fixed:** while the router waited on Claude, it held the database, so on the desktop app
  anything else that saved (a setting, an approval, another request) waited, and could fail
  after 30 seconds. It now asks Claude with the database free, and applies the answer in a
  short step afterwards (skipping a request canceled in the meantime).

- **When Claude is unavailable, it says so plainly and waits.** A reached usage limit pauses new
  tasks and requests until it resets, says when on Home, and nothing fails because of it.
  Overload, rate limits and network trouble are tried again a few times with a wait. An expired
  or missing sign-in says where to sign in again. Any other routing failure has a **Try again**
  button. See [Claude is unavailable](docs/troubleshooting.md#claude-is-unavailable).

- **Reads don't ask to go ahead.** A request that only reads a project with a production
  connection starts at once; "Go ahead?" is asked only before a change to production, which
  waits for your approval anyway. In the self-test, a scenario that shouldn't be held fails
  if it was asked, and the report says when the self-test answered a go-ahead.

- **The project chosen at the top no longer overrides the request.** An @-mention of another
  project or client in the request wins, and a request clearly meant for another client's
  project asks once, with both as buttons, instead of running in the chosen one.

## 0.1.0-beta.33 (2026-11-09)

- **A service you host for clients.** A client's connection can become your own tool
  (Details › Change › Make it my own tool), and an n8n link can be narrowed to one workflow
  tag or n8n project. The gateway checks it on every call: a client's project lists, reads
  and changes only its own workflows and runs, and never sees another client's workflow
  names. See [A service you host for clients](docs/connections.md#a-service-you-host-for-clients).

- **Fewer "which project?" questions.** It never asks with one option, doesn't ask again
  in a conversation where you've answered, and sends work any of a client's projects would do
  (their email, Google, Slack) to the client's **default project**: their only one, or the one
  you pick on the client's page. When it does ask, it says why in one line, shows projects by
  name, and offers to make your pick the default. See
  [When it asks which project](docs/concepts.md#when-it-asks-which-project).

- **Chats work like a conversation.** In Slack, Discord and Telegram, your messages
  continue one conversation, so "publish the result here" or "now do it for Acme" knows
  what came before. A new one starts after 2 hours of quiet or when you send **new**, and its
  first reply links to it in the app. A task you asked for from a chat posts its actual result
  there (tables as lines, with a link when it's long), and its approvals are posted in that
  chat with their buttons. See [A chat is a conversation](docs/notifications.md#a-chat-is-a-conversation).

- **Honest outcomes.** A task that couldn't do what was asked ends as **Not done**, with its
  reason, on its card, its page and in the chat it came from, instead of "Done". When it lacked
  a connection the client has, it offers one click to link it to the project and run the task
  again, in the app and as a chat button.

- **Plainer chat messages.** "Which project?" is asked once, with one line of why, and its
  buttons show project names. The "I need to know which project" line is gone. Pressing a
  button says "Approved.", "Denied." or "OK: Website.", and a decided card shows how it
  ended ("✔ Approved by Alex (Slack). It ran.") instead of adding a line to the old one.

- **Tidier display.** Task cards show the first sentence or two in plain text, without
  Markdown or tables. A long table on a task page stays one table to its last row, even when
  its rows came from several pages. New Google connections are named "Google · <client> ·
  <email>", and a connection that shares its name with another offers a clearer one.

- **Requests that need no client connection.** The client and project chosen at the top are
  a firm choice for new requests ("Sending to Alexey › My Project" under the box). Web
  lookups, writing and summaries run in the chosen project, or in your own client's default
  project (client page › Settings › Your own work). A project whose tasks can't use the web
  says so plainly. See [Where a request goes](docs/concepts.md#where-a-request-goes).

- **Follow-ups answer the open question.** After "which project?", a reply with just the
  name ("Blog", "Alexey, My Project") runs your request there, with no second question, in the
  app and in chats. "Yes" or "do it" is the go-ahead on production, and a reply naming a project
  places a request it couldn't place. Short follow-ups like "use my own Google instead"
  continue the open request instead of asking what you'd like.

## 0.1.0-beta.32 (2026-11-09)

- **Fixed:** a question about Ringkeeper sent from Slack, Discord or Telegram, such as "how
  many connections and clients do we have?", got "Nothing to do." It now gets the answer the
  app shows, in the chat it came from. A question is no longer told "Working out which project
  it's for…"; it gets "Looking into it…".
- **Fixed:** answers about your setup, and a few messages when connecting, named a client by
  its ID (general-informatics-llc) instead of its name (General Informatics LLC).
- **Slack setup:** an ID starting with D or C is a conversation's, not yours. Ringkeeper now
  says so, and where to find your member ID.

## 0.1.0-beta.31 (2026-11-08)

- **Cal.com.** A new tile: sign in with your Cal.com account through Cal.com's own hosted
  server. Reading runs. Bookings and every other change wait for your approval each time, and
  deleting an event type or a schedule needs the name typed. A project can be kept to one team.
  See [Cal.com](docs/services/calcom.md).
- **Shared recipes** are published in
  [ringkeeper-recipes](https://github.com/alexyushkin/ringkeeper-recipes). Import from a link
  shows an example from there, and links to it.
- **Fixed:** on Routines, a project with no agents yet now offers the recipes too, not only
  Create an agent.
- **Google Docs** on the Google account: read a document, make one, and edit one by replacing
  text or adding to the end. Making and editing wait for your approval, and an edit always asks,
  since a document may be someone else's. **To use it, sign the account in again:** under
  Connections, open it, choose Change access and tick Docs. If your Google app is older, turn on
  the Google Docs API first. See [Google](docs/services/google.md#docs).
- **PageSpeed Insights.** A new tile, with a free Google Cloud key: a page's scores, Core Web
  Vitals and the fixes that would save the most time. It only reads. See
  [PageSpeed Insights](docs/services/pagespeed.md).
- **Two new starter recipes:** a weekly speed check, and a monthly search and speed report.
  A recipe can now ask for one business system by name (`vendor:pagespeed`), not only a type.

## 0.1.0-beta.30 (2026-11-08)

- **Recipes.** An agent and its routines as one file, to start from or to share. New routine
  shows 13 starter recipes, and imports one from a file or an https link: you see all of it
  first, choose the project's connections, and its routines start paused. An agent's page
  exports it as a recipe, without keys, connection names or what its events watch. A recipe
  can't run code. See [Recipes](docs/recipes.md).
- **Send a request for later.** Under the request box on Home, **Send later…** picks a time: the
  request waits until then, and Home lists what's waiting, each with **Cancel**. If Ringkeeper
  isn't running at that time, it runs when Ringkeeper starts, unless that's more than 6 hours
  late; then it doesn't run, and its conversation says so.
- **Snooze an approval.** An approval can be snoozed for an hour, three hours or until tomorrow
  at 9: it leaves Approvals, its count and notifications until then, and comes back with a fresh
  notification. Snoozed ones are at the bottom of Approvals, with **Wake it now**. A snooze never
  runs past the time the approval expires.
- **Send new requests from Slack or Discord.** Turn on **Take new requests by DM** on a Slack or
  Discord channel (Settings > Notifications), and a direct message to the app from you becomes a
  request, the same as one typed in Ringkeeper. What happens to it comes back in that DM. Anyone
  else is told no. A Slack app set up before this needs its manifest pasted again and a
  reinstall.
- **A plan's card says the model and the likely cost.** Before you approve a plan, it shows the
  model that would do the work and what finished tasks like it cost (the middle of the range,
  from this project's own, or every project's when it has fewer than three), what the plan cost
  so far, and the task's limit.
- **A Forward an email recipe.** Forward an email to yourself with a note on top saying what to
  do, and Ringkeeper does it. It watches only mail from you to you with Fwd in the subject, and
  treats the forwarded part as information, never as instructions.
- Approve and Deny on Windows notifications aren't possible yet: the desktop app's notifications
  only offer buttons on phones. Telegram, Slack and Discord have them.

## 0.1.0-beta.29 (2026-11-07)

- **A client's month.** Each client's page has a This month tab: tasks and how they ended,
  changes made and approved, refused calls, and the AI cost, for any month. Download it as a PDF
  or Markdown report to send the client, or the tasks as CSV. Or draft it as an email from one
  of their mail connections: it waits in Approvals until you approve it. Clients & projects
  shows each client's cost this month. See [A client's month](docs/client-month.md).
- **A monthly cap per client.** Set it on the client's page. Once a month's AI cost reaches it,
  a new task or follow-up for that client waits in Approvals: run it anyway this once, raise the
  cap, or don't run it (both ways of going on are in Activity). Routines skip their runs, and
  Home says which client's routines are waiting.
- **Passes: use a project's connections from Claude Desktop, Claude Code or Cursor.** Settings >
  Passes makes a pass for one project. Claude Desktop gets it as an extension file to double-click;
  Claude Code a command; Cursor a few lines of settings. Every call goes through Ringkeeper the same
  way a task's does: only that project's connections, never another client's, changes to production
  held for your approval, and undo for 7 days. When a change is held, the app gets a link to it in
  Approvals to give you. See [Passes](docs/passes.md).
- **An app with a pass can hand Ringkeeper a whole request.** Its `ringkeeper_ask` tool starts a
  task in the pass's project, with the usual approvals, limits and audit log, and you see it in
  Ringkeeper like any other. The app gets the result if it's done within 50 seconds (a setting
  under Settings > Passes, 50 at most), the task's
  link otherwise (and `ringkeeper_task_status` to check later), or the link to Approvals if a
  change waits for you.
- **Claude Desktop keeps its pass across a Ringkeeper update.** The extension runs Ringkeeper from
  its install folder, and the bridge stops when Ringkeeper quits, so it doesn't hold up an update.
  Claude Desktop starts it again once Ringkeeper is back; Claude Code and Cursor reconnect it.
- **Automation watch.** A routine can start when a client's n8n workflow run fails, a
  Vercel deploy fails, a GitHub check fails or an issue is opened, or a website stops
  answering. It's checked every 5 minutes, reading only, through the project's own
  connections. A new template, **Fix a failed automation**, finds out why and proposes a
  fix for your approval. Home says what's being watched and the last problem found.
- **Routines that watch Airtable or Gmail now read through Ringkeeper's checks too,** like
  a task: only the project's own connections, never another client's.

- **The conversation says what happened.** When Ringkeeper asked which project a request was
  for, its reply could still start with "I'll read the request...", as if the work had begun.
  Now a reply that asks only asks. And once you pick the project, the conversation says
  "Working on it in ..." instead of leaving the question as its last word (or that nothing
  started, if you chose none).

## 0.1.0-beta.28 (2026-11-07)

- **A client's connection works only for that client's projects, on every call.** It was
  checked when you linked it in the app, but a registry file could link it to another
  client's project, and the gateway didn't check again. Now a registry file is refused the
  same way, and the gateway refuses such a call even if the link is already there. If
  `registry apply` now refuses your file, give each connection the owner of the projects
  that use it, or make it a shared tool.
- **Approvals show the values.** A held database change showed `status = %s`, with the values
  only under Show details. They're now right under the statement.
- **Chat apps say where you answered in plain words:** "in the app", not "Api".
- **Run tasks in Docker.** With Docker Desktop, Settings > Security can run each task in a
  container that sees only its project's folder, away from the vault, the keychain and
  your other files. It's off by default, and recommended for client work. If Docker isn't
  running when a task starts, the task runs on this computer and says so. The first task in
  Docker also downloads Claude Code (about 130 MB), once.
- **Self-test.** Settings > Advanced > Self-test, or `ringkeeper scenarios run`, sends real
  requests through Ringkeeper against two made-up clients in a scratch folder, and reports
  where each went, what was held, what was refused, and whether changes could be undone. Two
  of them push the model to reach the other client, and the report shows what it did.
- **"Remember this" is remembered.** It used to end up in a notes file on a task's branch, where
  the next task couldn't find it, even though the reply said it was noted. Now it goes to the
  project's memory.
- **An agent you name is the one that runs.** A task you gave to an agent could run without it,
  with wider access than that agent has, if the job didn't look like one for it.

## 0.1.0-beta.27 (2026-11-06)

- **Searches in connected services find what was asked.** A search written the natural way, such
  as a name and a limit in HubSpot, failed with a technical error, and the agent's next try could
  go out with no filter at all. Values now go where each service reads them, and agents are told
  where that is.
- **A call that can't run says why in plain words,** on every tool, and no longer repeats what was
  sent.
- **Meta's setup steps match Meta's screens.** They now say where to add each permission, which
  stops the Invalid Scopes error at sign-in, and where the redirect address goes.

## 0.1.0-beta.26 (2026-11-06)

- **Leadfeeder.** Website visitors, companies, contacts and lists, through Leadfeeder's own server,
  with each project held to one account. Reads run, changes wait for approval, and anything that
  may spend credits waits with the cost on the card. Deletes need the name typed.
- **Add a tool to everything at once.** Right after adding one of your own tools, you can use it
  in all projects, with new clients and projects getting it too, and leave out any that shouldn't.
- **Every service has its own mark.** Brands' own logos are bundled with the app, with light and dark
  versions where they exist, and the brand's color with initials where a logo can't be used. They
  show in the catalog and on connection cards.
- **Plan limits are said plainly.** When any service answers that your plan doesn't include
  something, the connection's card and the task say so, and the call isn't repeated. That call
  doesn't count as a failed attempt or move the task to a bigger model. Anything else that fails
  in the task still does.
- **"Trust for 1 hour" no longer shows** on services that can't be trusted, like ElevenLabs.

## 0.1.0-beta.25 (2026-11-06)

- **Agents remember between runs.** Each project keeps notes, here by default or in a Notion
  database you can read and edit. An edit there wins, a deleted row is forgotten, and what you or
  other tools add is picked up on the next run. Agents reach only their own project's notes, and
  secrets and card numbers are refused.
- **My memory.** Your own preferences across clients, in a Notion database that works on any
  Notion plan. Agents search it when they start and add to it when they finish. Settings has a new
  Memory tab for it, along with a shared notes page and Notion AI memory, which needs Notion's
  Business plan.
- **Nothing about a client goes in your own memory.** A save naming a client or a project's full
  name is refused, one common word like "website" never counts, and emails, phone numbers and
  street addresses are taken out. A project's own notes may keep a business contact's email.
- **Tools are read again after signing in,** so what a server newly offers shows up.

## 0.1.0-beta.24 (2026-11-06)

- **Long lists have Load more.** Conversations, tasks, activity, routine runs and approvals load 50
  at a time, and nothing is out of reach any more. Search covers everything, not just what's
  loaded, and search and Archived say how many there are. Activity has a search box now, and
  task search also finds the words of a task's conversation.
- **Counts are real.** The Approvals badge and a project's task count no longer stop at 200 and
  100.
- **Spend cards say what it is.** A Google Ads card reads "Budget: Spring promo", "Campaign",
  "Ad group" and "Customer", by Google's own names, with the ID in small text. Other vendors'
  cards say "Invoice", "Transaction" or "Order" where "To or from" and "Account" were wrong.

## 0.1.0-beta.23 (2026-11-06)

- **Social accounts.** Bluesky, Facebook Pages, Instagram, Threads and YouTube can read posts,
  comments and insights. Anything unpublished or scheduled waits for approval. Publishing,
  replying and deleting need the connection's name typed. Your own Meta accounts work now; a
  client's needs Meta's review of your app, and the tile says so.
- **YouTube videos are private unless you choose otherwise.** Ringkeeper always sends the
  privacy, because YouTube's own default is public.
- **Google Ads.** Reports run, and pausing waits for approval. Creating, starting, and changing a
  budget or a bid need the name typed, and the card shows the spend. Settings shows the daily
  operations every client shares.
- **A new Google Ads campaign's card shows what you declare.** Approving one tells Google it has
  no EU political advertising, so the card says that in full. A campaign that has any can't be
  made here.
- **Help > Ask is a conversation.** Ask a follow-up about an answer, or press New question to
  start again. It stays while you open a page from an answer and come back; reloading or
  signing out ends it.

## 0.1.0-beta.22 (2026-11-05)

- **Scanned PDFs and pictures can be read.** The first time a task meets one, the app offers
  the scan reader, a one-time download. It reads English, on this computer, and nothing leaves
  it. If you say Not now, it's in Settings > Reading scans. On a Mac it needs macOS 15 or later.
  Before this, a scanned PDF couldn't be read even with Tesseract installed.
- **Security and Help say plainly** that the desktop app isn't a sandbox: the AI runs as your
  Windows or Mac user, and server mode is the choice for strong isolation.

## 0.1.0-beta.21 (2026-11-05)

- **ElevenLabs,** through its official hosted server. Signing in is all it takes. Reads run;
  agents, calls, voice clones and speech wait for approval; deleting needs the name typed.
- **Intuit, Xero, HubSpot and Salesforce sign-ins** now come back through
  `https://yushkin.us/ringkeeper/oauth/callback`, a static page that hands the browser to
  Ringkeeper on this computer. Add that address to your app at the vendor before signing in
  again. Intuit's production keys no longer end with pasting an address. Accounts already
  connected keep working.
- **Signed links are removed** from logs, the audit log, tool results and conversations:
  Lemon Squeezy customer portal links, S3, Google Cloud and Azure download links, and the like.
- **Long emails and files read in full.** Mail tools for Google, Microsoft, Gmail and Mailbox
  now return the whole message, up to 200,000 characters at a time, as clean text with links
  and tables kept, and the rest in parts. A reply's quoted messages are kept apart. Tasks can
  read attachments (PDF, Word, Excel, CSV, and pictures with OCR) and whole conversations.
  Long files, SQL rows and API answers come in parts too, and a task is told to read the rest
  or to say it has only part.
- **Tasks no longer hold a model key.** Workers call Ringkeeper's model proxy with a token
  that only reaches the model and ends with the session, and Ringkeeper adds the real key on
  the way out, whichever place Claude runs in. Each task's tokens are counted there.
- **Where Claude runs.** Settings › Claude can run tasks, the architect and the router on
  Amazon Bedrock, Google Vertex AI or Microsoft Foundry, with a live check. A project can
  use another place.
- **Clouds.** AWS, Azure, Google Cloud and OCI connect from the login you already have in
  their command line tools. Each runs the cloud's own server on this computer, so
  Ringkeeper holds no key. What a command needs comes from the command itself, and each
  project's link names the client's own subscription, project or compartment.
- **Oracle Database,** with its wallet for an Autonomous Database. The same checks as
  Postgres, and a plain update or delete can be undone.

## 0.1.0-beta.20 (2026-11-05)

- **Business systems.** HubSpot, Salesforce, Pipedrive, Keap, Jobber, Housecall Pro,
  ServiceTitan, QuickBooks, Xero, Wave, Business Central, BILL, Shopify, Ghost, Square,
  Helcim, Lemon Squeezy, Clover, Braintree, Plaid and Clerk, plus official servers for
  Stripe, PayPal, Webflow, Sanity, Contentful and more. Each built-in one has only the
  actions Ringkeeper knows, so what isn't listed can't be called.
- **Microsoft accounts.** Mail, calendar, OneDrive and SharePoint files, through your own
  Entra app, with the same holds as Google.
- **Settings › Developer apps** holds your own registered apps for Google, Microsoft,
  HubSpot, Salesforce, Xero, Intuit and Jobber, with the steps, the address to paste, the
  scopes and a live check. Many accounts sign in through one app.
- **Trusting an action for an hour now covers that one action.** On an add-on tool it used
  to cover everything else that connection could do without the name typed.
- **An approval that moves money shows it in large text:** amount, currency, counterparty
  and account, as written in the call.
- **Rules that can't be loosened.** On CRM, accounting, CMS and payment servers a per-tool
  setting can make a tool stricter, never looser. Nothing on an accounting connection runs
  by itself.
- **Gmail and Google accounts follow the same email rule as every other connection.**
  One email waits for your approval, and more than 10 people at once needs the connection's
  name typed, never from a chat app. A Google account can no longer let sending run without
  asking; a setting that did stops counting.
- **The Forms On Fire tile is marked Unverified** until its address and sign-in are
  confirmed.

## 0.1.0-beta.19 (2026-11-05)

- **More connections.** Email sending through any SMTP provider or Resend, any mailbox
  (Gmail, Outlook, Zoho, Yahoo or your own IMAP server), any HTTP API (with Forms On Fire
  and DataForSEO), and file storage (AWS S3, Cloudflare R2, Vercel Blob). Google accounts
  can read Search Console and Analytics. Connectors are now grouped by what they're for,
  with Built in, Official and Community marks.
- **Use a connection in all of a client's projects.** Projects added later get it too,
  with exceptions if you want, and a project's link can still be narrowed.
- **Ringkeeper finds what you already have.** Logins on this computer (GitHub CLI, Vercel
  CLI, and AWS, Azure, Google Cloud and OCI, listed for now), MCP servers in a project's
  folder and in Claude Code, Codex and Cursor settings, and many more keys in `.env` files.
  Each is offered once and added only when you say.
- **Card and bank numbers stay out.** A tool call carrying one is refused, and they're
  removed from logs, conversations, a task's events and the audit log.
- **Voice on Intel Macs.** The Intel build can now download voice like the others. Older
  processors take longer to turn speech into text.
- **Cmd+Q stops all of Ringkeeper on a Mac.** The background part kept running, so a newly
  installed version could connect to it and go on showing the old version number. A new
  version now also replaces whatever an older one left running.
- **Opening it on a Mac.** The app isn't signed yet, so the first time, in Finder, choose
  Go › Applications, right-click Ringkeeper, and choose Open. The install guide and the
  download page now say so.

## 0.1.0-beta.18 (2026-11-04)

- **Older Macs.** The app's pages are now built for the web engine in macOS 12, so they
  no longer risk stopping at Loading there. The build fails if that ever changes.
- **Your Claude Code login on a Mac.** "Or use my Claude Code login" now works on Macs too,
  reading it from your Keychain. macOS asks you to allow that.
- **Changes are saved before the app answers.** A change could take a moment longer to
  save than the app took to reply, so the next screen could briefly show the old value.

## 0.1.0-beta.17 (2026-11-04)

- **Intel Macs.** There's now a download for Macs with an Intel processor, beside the
  one for Apple Silicon. Voice isn't available there: the speech engine no longer
  supports them. Like the other Mac build, it's unsigned and experimental.
- **Replies read cleanly everywhere.** Task cards, Telegram, Slack and the digest showed
  Claude's formatting as raw stars and backticks. Now they show plain words.
- **Checkboxes keep their size.** Next to long text they were squeezed narrower than
  the others.
- **A new picture of how it works.** Help and the guide now start from what you actually
  do: you ask, Ringkeeper finds the project, Claude does the work, and live changes wait
  for you. Agents and routines are shown as the optional extras they are.

## 0.1.0-beta.16 (2026-11-04)

- **The filter says when it's on.** With a client or project chosen in the top bar,
  Home, Tasks, Approvals, Agents and Connectors now say "Showing Acme Windows only" with
  Show everything beside it, so nothing looks gone. The last 24 hours follows the filter
  too, and Approvals no longer says nothing is waiting when other clients have requests.
- **Client connections add up.** A client now counts your own tools its projects use,
  and its page lists them, so a project with a connection no longer sits under
  "0 connections".
- **A clearer light theme.** Cards stand out from the page, quiet text is darker, and
  boxes you type in or choose from have a border you can see. In the dark theme too.
- **New projects reach the switcher.** The switcher and the client counts refresh as you
  move between pages and come back to the window, without restarting the app.

## 0.1.0-beta.15 (2026-11-03)

- **Project names.** A project now has a name as you typed it, shown everywhere: pages,
  approvals, messages and the digest. The short name stays for links and the AI, and
  moves to Project › Settings. Existing projects get a name from their short one:
  change it under About this project.
- **Logos.** Connectors show each service's logo (from Simple Icons), and initials
  where there is none.
- **Hide the menu.** The sidebar's toggle is now just the panel icon.
- **Quick request.** Ctrl+Alt+R opens a small box over whatever you're doing, with
  voice and the client and project switcher. Send, and it shows a link to the
  conversation and closes. Change the key in Settings › Advanced.
- **@-mentions.** Type @ in a request box to name a client, project, connection or
  agent. The request goes there, whatever else it says.
- **Keys already in a project.** A project's .env and config files are offered as
  connections: masked, one confirmation each, never from outside its folder.
- **Connection health.** Every connection is tested daily, reads only. You hear if one
  stops working, or 7 days before a key expires. Test all is on Connectors.
- **Faster approvals.** J, K, A and D on the keyboard, Approve all N like this, and
  trusting an action in a project for an hour. Never for deletes.
- **Morning digest.** Home shows what ran, failed and waits, per client; Slack,
  Telegram and Discord can get it at a set time.
- **Run again, and saved requests.** On every task. Saved requests are on Home.
- **Files on a request.** Drop them on the box: they go into the project.
- **Pins and recents.** In the switcher and Ctrl+K.
- **Help that knows the guide.** Ask how something works in the request box or in
  Help › Ask. It answers from this version's guide and your setup, says so when
  something doesn't exist, and ends with the next step.

## 0.1.0-beta.14 (2026-11-03)

- **Connectors.** Connections is now Connectors, and Browse connectors opens the
  directory: built-in services first, as tiles that open their form; then
  services you sign in to; then a search of every other connector, with
  publishers and verified badges, the service's own first and community servers
  folded away.
- **Is this live client data?** Asked every time you add a client's connection,
  with no default, and it says what each answer lets run.
- **Readable names.** Connections read like "n8n · n8n.geninfos.com" or "Google ·
  name@gmail.com", on every page and approval. Existing ones are renamed, and you
  can change a name under Details › Change. Task transcripts read like "Database:
  execute".
- **Linking in one click.** A connection's card says where it's used and has Link
  to project…; a project's Connections tab has Link a connection. A client with
  no projects says so, with Add project and From Claude.
- **From Claude, tidied.** Most recent first, grouped by likely client, with a
  search. A Claude project named like a folder shows once, and folders that only
  hold other projects are folded away.
- **Your subscription first.** With personal subscription mode on, first run and
  Settings › Claude start with your subscription.
- **Google accounts in one place.** Settings › Google app lists each account with
  its client and projects, and Add a Google account is right there. New accounts
  can write drafts unless you untick it.
- **Results where you look.** A task's card and a routine's run show what it said
  at the end. Tasks offers to archive failed tasks older than 7 days.
- **No dead ends.** Agents and routines offer Add project when there are none, and
  a project's new routine offers Create an agent right there.
- **Deep folders.** A task no longer fails with "Filename too long" in a deeply
  nested project folder on Windows.

## 0.1.0-beta.13 (2026-11-02)

- **Formatted replies.** Ringkeeper's replies, task transcripts and questions
  show their bold, lists, tables, code and links instead of the raw `**` marks.
- **Room to work.** Pages use the window's width (up to a comfortable limit)
  instead of a narrow column. The menu can shrink to icons only: Icons only at
  its foot, and it stays that way.

## 0.1.0-beta.12 (2026-11-02)

- **The tour stays skipped.** Skipping or finishing the tour no longer starts
  it again each time you go back to Home.
- **Every Settings tab in view.** About was cut off at the end of the tab row;
  the tabs now fit, and wrap onto a second row in a narrow window.
- **New examples.** Forms and Help use General Informatics LLC and
  geninfos.com as their examples.
- **Undo you can count on.** An approval says "you can revert this" only after
  checking: the table has a primary key, the Airtable records and the n8n
  workflow can be read. Otherwise it says why it can't be undone, before you
  approve.
- **Mac (Apple Silicon), experimental.** The install guide now covers the Mac
  build: unsigned and not yet tested on a real Mac, so macOS asks you to allow
  it once (System Settings › Privacy & Security › Open Anyway). Reports welcome.

## 0.1.0-beta.11 (2026-11-02)

- **Undo for data changes.** Before an approved or auto-run change to a
  database (a plain UPDATE or DELETE), Airtable records or an n8n workflow,
  Ringkeeper keeps what it's about to change (only that) in the data folder.
  For 7 days, the task page and its conversation show Revert with what it puts
  back; reverting waits for approval like any change, and stops if the data
  changed again meanwhile. Approvals say beforehand when something can't be
  undone (a sent email, a text, a deploy, an INSERT). Kept changes expire after
  7 days; each project has 50 MB for them.
- **Backup and move.** Settings › Backup saves everything (clients, projects,
  connections and their keys, agents, routines, settings, history) into one
  file locked with a password you choose. Restore it on another computer: see
  what comes in first (and which project folders aren't there yet), then
  restore and restart; what was there is kept beside it. Project folders are
  referenced, not copied. Optional weekly backups to a folder you choose,
  keeping the last few.
- **Report a problem.** Help › Report a problem (or Ctrl+K) gathers the
  version, your system, a few counts and the end of the logs, removes keys,
  tokens, passwords and email addresses, and shows you all of it, editable.
  Then open a prefilled GitHub issue, or save it as a zip. Nothing is sent by
  Ringkeeper itself.
- **What's new.** After an update, Home shows what came with it, once.
- **A user guide.** docs/ has a page per idea and per service (where to get
  its key, what waits for approval, what can be undone), installing on Windows
  or a server, backups, security (what's stored where, and what leaves your
  computer), troubleshooting and a FAQ. The README is one screen.

## 0.1.0-beta.10 (2026-11-02)

- **Get started on Home.** A checklist (Claude connected, a first project, a
  first connection, a first task) links to each step and goes away when it's
  done, or when you hide it. Below it, three requests to try, built from your
  own projects and connections, that only read.
- **Demo data.** "Try it with demo data", in the first run and on Home, adds a
  made-up client, Demo Co, with a shop project: sample files in its folder and
  a small shop database, set up as production so a change waits for your
  approval. Try tasks and see a hold before connecting anything real. Remove
  the demo removes all of it, its tasks and conversations too.
- **Ctrl+K.** A command palette from anywhere (or "Search or jump to" in the
  sidebar): switch client or project, start a request, add a connection or a
  shared tool, a new agent or routine, open pending approvals, any page or
  setting. Type a few letters of what you want.
- **Help.** A Help page in the sidebar: the seven ideas (client, project,
  connection, shared tool, agent, routine, hold) in plain words with a picture
  of how they fit, the user guide, and Report a problem. A three-step tour
  after the first run shows the request box, Approvals and Connections; skip
  it, or see it again from Help. A "?" beside Connections, Agents, Routines,
  Settings › Google app and Notifications says what each is for and links to
  the guide.
- **Plain words everywhere.** Errors say what happened and what to do next,
  without internal IDs, command-line flags or developer words (the
  connection's and client's names instead). Telegram, Slack and Discord
  messages lose their short IDs and raw statuses ("✖ acme-web: stopped at its
  spending cap", "Approved by alex (Slack)"). Approvals name the action
  ("Changing Airtable records on acme-crm"). Empty pages say what goes there
  and how to add the first one. Errors that used to vanish (a failed remove,
  a failed toggle) now show.
- **Agents in the sidebar.** An Agents page lists every project's agents,
  narrowed by the client and project switcher, with New agent (it asks which
  project first). The Routines page has New routine: pick the project, then
  its agent, or create one right there if it has none. Empty pages say how
  they fit: an agent is who does the work; a routine is when it runs.

## 0.1.0-beta.9 (2026-11-01)

- **Agents.** Each project can have named agents: instructions they always
  follow, the connections they may use (some of the project's; shared tools
  only when ticked: the gateway refuses the rest), and their own model, effort
  and autonomy. Ask one in a conversation: "ask the Seaside follow-up agent
  to...".
- **Routines.** A routine runs an agent on a schedule (every day, weekdays,
  chosen days, monthly or every few hours, in your time zone), when something
  happens (its webhook is called on this computer, a file is added to the
  project's folder, a new Airtable record, a new matching email), or when you
  run it. Start from a template: daily digest, new-lead follow-up draft,
  weekly report, data sync check. New routines start paused with a Test run.
  Each run is a task: reads run, actions wait for your approval (in Approvals,
  the routine's page and notifications). Limits per run: steps and minutes, and
  a dollar cap with an API key; and runs a day. After the computer slept, one
  run makes up for the missed ones and says so. Pause one routine, a project's,
  or all of them (the tray's "Pause all routines" now pauses routines only;
  "Stop everything" stays separate). Every run is in the routine's history,
  with an optional summary to your notification channels.
- **Slack and Discord.** Settings > Notifications sets up your own Slack app
  (Socket Mode) or Discord bot, step by step; neither needs anything on this
  computer opened to the internet. Approvals and results arrive as a short
  summary with Approve / Deny / Open buttons. Approve is offered only where
  it's safe without the details (not production changes or deletes); only you
  can press them, and anyone else in the channel is told they're not allowed.
  Open brings Ringkeeper up at that page on this computer. Send test checks it.
- **Choose what each channel sends.** Telegram, Slack and Discord each pick
  approvals, routine results, failures or finished tasks, quiet hours, and
  which clients and projects.
- **Clients of their own.** A client is kept with its name as you typed it
  ("Seaside Windows & Doors"); the short form is only for links. Typing a new
  client anywhere creates it, and a client with only connections, or nothing
  yet, shows in the switcher, Clients & projects ("No projects yet", with Add
  project and its connections count) and every client picker. Clients from
  earlier versions keep their short names until you give them full ones:
  Clients & projects offers to. Rename a client in its Settings, or remove it
  (moving its projects and connections to another client first).
- **Rename a project** in its Settings. Its tasks, approvals and links follow,
  and the old name stays as one of its other names, so asking by it still
  finds the project.

## 0.1.0-beta.8 (2026-11-01)

- **Subscription mode reaches every task.** A task created by answering "which
  project?" (in the app or Telegram) was set to the API key and failed with "No
  Anthropic API key is stored". Tasks now use what's set in Settings when they
  start, wherever they came from.
- **Questions about Ringkeeper itself are answered by Ringkeeper.** "How many
  connections do we have?" went to a client's production database. Questions
  about your connections, projects, clients, tasks, approvals, settings and
  costs are now answered from Ringkeeper's own records: no project, no worker.
- **Asks before production.** When Ringkeeper isn't sure which project you
  mean, or the request would work with a production connection's data, it asks
  you in the conversation before starting anything.
- **Questions in the conversation, with buttons.** "Which project?" and "go
  ahead on production?" show right in the conversation, in plain words, not in
  Approvals and not in its badge. Held changes show there too, and on the task
  page: what it wants to do, the SQL, Approve or Deny with a note. Approving
  anywhere clears it everywhere.
- **Reading never waits.** Only writes wait for approval (changing data,
  sending, deploying, deleting, spending money). A SQL statement that only reads
  runs at once, even on production, and so do MCP tools whose names only read,
  unless they may cost money.
- **Google setup steps include Branding.** Google won't publish your Google
  app until its Branding page has a home page, a privacy policy and an
  authorized domain; the steps in Settings › Google app now say what to enter.
- **A privacy policy**: [PRIVACY.md](PRIVACY.md), also at
  <https://yushkin.us/ringkeeper/privacy>.
- **Test works for Google accounts.** It said "Google returned 404:
  invalid_request" though the account worked: it asked Google at the wrong
  address. Test again after updating to clear "Last test failed". A failed
  test now shows its reason on the connection's row, without opening it.

## 0.1.0-beta.7 (2026-11-01)

- **Updates no longer wait for an unrelated ringkeeper.exe.** The installer
  only waits for programs running from Ringkeeper's own folder, not for any
  program with the same name elsewhere. If one doesn't quit, the message says
  which one ("Ringkeeper is running from …") or which folder is in use.
- **The new icon everywhere.** The "Starting Ringkeeper…" screen and the logo
  beside the name in the app now use it. After an update, the taskbar and Start
  menu now show the new icon too, instead of the one Windows remembered from
  before.
- **Sharp installer images** at any display scale (125%, 150% and up), instead
  of a stretched, blurry icon.
- **A personal project, by Alexey Yushkin.** Settings › About, the license and
  the notices say "Alexey Yushkin and contributors", and Windows' list of
  installed apps names him as the publisher. An update from an earlier version
  still installs into the folder that version was installed in.
- **All projects, except…** A shared tool for all projects can leave out
  clients (all their projects, new ones too) and single projects. The
  connection says so plainly: "All projects except Acme (client),
  seaside-messages". Left-out projects don't see it, and the gateway refuses
  their calls to it. Turning "All projects" off drops the exceptions.
- **Google app, set up once; as many Google accounts as you need.** Settings ›
  Google app says what it is (connecting Ringkeeper to Google once), then shows
  **Add a Google account** and every account signed in, each linking to it.
  You can add more Google apps: a client's own Google Cloud project (for a
  Workspace admin who only trusts their own) or another 100 accounts. Each
  account picks the app it signs in through; the first is the default.
- **Projects from Claude Chat and Cowork.** Besides the folders you opened in
  Claude Code, adding a project now offers the folders you picked in Cowork and
  the Claude app's Code tab, and your Claude projects: each gets a new folder
  with its instructions (as CLAUDE.md) and its files.
- **Several voice languages.** Settings › Voice takes a few languages; each
  recording is detected among just those.
- **Removing asks first, in the app.** Removing a project, a connection or a
  Google app, and Stop everything, ask in place before doing it. The desktop
  window didn't show the browser's confirmation, so one click did it.
- **No scroll bar under the tabs** (Settings and other tabbed pages).
- **Light or dark, your choice.** Settings › Appearance has a Theme: System
  (as before, it follows Windows' light or dark setting), Light or Dark. It's
  kept for this computer, or per browser on a server install.

## 0.1.0-beta.6 (2026-10-31)

- **Every kind of connection can be yours or a client's.** A database, n8n,
  Airtable, Vercel, Twilio, Gmail, GitHub or Google connection, like an MCP
  server, is either a client's (only their projects can use it) or your own
  (any project, or all of them). Add as many of each as you like.
- **GitHub connections**: a token (the form says which repositories and
  permissions to give it), on github.com or your GitHub Enterprise Server. Pin a
  repository per project and the agent can't reach the others. Reading runs;
  issues, comments, pull requests, merges and pushes wait for your approval. A
  task's branch can be pushed and a pull request opened with that project's
  token, never your computer's git credentials.
- **Retell AI** is on the Recommended shelf: Retell's own MCP server with an API
  key. Reading agents, calls and numbers runs; creating or updating agents,
  placing calls and buying numbers wait for your approval; deleting needs the
  name typed.

## 0.1.0-beta.5 (2026-10-31)

- **Shared tools are easier to add.** Add a connection asks first: "A client's
  account or your own tool?", and only a client's account asks whose it is.
  Connections has **Add a shared tool**, which opens the catalog for shared
  tools only.
- **Remote servers can be shared tools** too. The catalog suggests a client's
  account when a server wants a sign-in or a key, and your own tool when it
  answers with nothing; a server that needs nothing connects without a key.
- **Google accounts** (desktop app): Gmail, Drive, Calendar and Sheets for
  your clients' Google accounts and your own, through a Google app you create
  once in your own Google Cloud project (Settings › Google walks you through
  it). Each account is its own connection with the access you choose; a
  client's stays with that client's projects, yours can go to any or all. A
  project can have several, with one as its default. Reading runs; every write
  (sending, sharing, invites, edits, uploads, drafts, the trash) asks you
  first, and you can let a tool run without asking, per account. The Gmail
  app-password connection stays as the simple option.
- **A new icon**: a key ring with three keys, on a navy tile. In the taskbar,
  the tray (with its red dot when something waits for you), the installer, and
  the browser tab. At small sizes the keys are drawn thicker so they stay apart.
- **Check for updates** in Settings › About and in the tray menu. It shows
  this version, when it last checked, and the result: "You're up to date",
  "Update available" with the release notes and **Install and restart**, or
  what went wrong in plain words (offline, a signature that didn't verify).
  While tasks run, it offers **Restart when tasks finish** instead: it never
  restarts mid-task. **Check automatically** (on by default) can be turned off.
- **Voice has a Language choice.** Settings › Voice: Auto-detect, or a
  searchable list of the speech model's languages. With the Fast model and a
  language other than English, it suggests Accuracy, which handles other
  languages much better.

## 0.1.0-beta.4 (2026-10-30)

- **"Use my Claude Code login" says what it found.** An old login says
  "Found an old Claude Code login from <date> that has expired" and points to
  `claude setup-token`; no login says so, and that the Claude desktop app keeps
  its own. The `setup-token` token comes first in setup and Settings: it lasts a
  year.
- **Shared tools.** A browser, files on this computer, a fetcher: MCP servers
  that hold no client's data can now be your own tool, linked to any client's
  projects or to all of them (new projects get them too). A client's
  connections are still only for that client's projects. Their writes still
  wait for your approval.
- **Importing from Claude Desktop** asks, for each server, whether it's a
  client's account or your own tool (it suggests one), and gives it to several
  projects at once, or to all projects for a shared tool. Skip is still there.
  The catalog's local servers and extension files ask the same.
- **After adding a connection** (a client's page, a project's page, Add a
  connection), a card offers **Also use in…** for the other projects that may
  use it (narrowed per project where the service allows) and **Add another
  connection**.
- **First run:** step 2 has **Browse the catalog** next to the Claude Desktop
  import, and the connection step takes as many connections as you like before
  Continue, listing the ones added.
- **Shorter pages.** Project and client pages have tabs (Overview, Connections,
  Tasks, Settings), and Settings has tabs too (Claude, Models, Voice,
  Notifications, Advanced, About). Sections fold away and show a one-line
  summary while folded; what's open is remembered per page. Connections, catalog
  results and the Claude Desktop import list are compact rows that open for
  details.
- **Installing a newer version over an older one** no longer asks "uninstall
  before installing / do not uninstall": it upgrades in place, keeps your data,
  and goes from progress straight to Finish. Reinstalling the same version, or
  installing an older one, still asks.
- Changing a connection's kind of data no longer drops an MCP server's saved
  settings (its tool rules and package).

## 0.1.0-beta.3 (2026-10-28)

- **First run can use your Claude subscription** when personal subscription
  mode is on (`RINGKEEPER_ALLOW_SUBSCRIPTION=1`): step 1 offers it next to the
  API key, with a token from `claude setup-token` or, in the desktop app, your
  Claude Code login. Without it, step 1 asks for an API key only.
- **Server installs follow the same rule in Settings:** the subscription is
  offered only with `RINGKEEPER_ALLOW_SUBSCRIPTION=1`. An install already on
  its subscription keeps running and shows a one-line notice to set it.

## 0.1.0-beta.2 (2026-10-27)

- **Install extension.** Claude Desktop extension files (`.mcpb`) install from
  the catalog: open the file, check what it says it is (its publisher, tools,
  the command it runs, the settings it needs), confirm once, and it's unpacked
  into Ringkeeper's data folder and connected. It follows the same approval
  rules as an extension imported from Claude Desktop.
- **First run says what Ringkeeper is for:** one person running AI agents
  across many clients without switching connections.

## 0.1.0-beta.1 (2026-10-26)

The first beta of the desktop app for Windows.

- **One installer, nothing else to install.** Ringkeeper installs for your
  user only (no administrator rights) and brings what it needs with it:
  Python, git for its workers, and the Claude Code engine its agents run on.
  Your data stays in `%LOCALAPPDATA%\Ringkeeper`.
- **Projects bound to their own services.** Each client's projects reach only
  that client's databases, Airtable bases, Vercel projects, n8n and other
  connections, connected in one click where the service allows it.
- **An open catalog of MCP servers.** Search every server in the official MCP
  Registry (it works offline from what you've seen), connect remote ones with
  one-click sign-in or a key, install local ones (npm, PyPI, containers)
  pinned to the version shown after one confirmation, or add any server by its
  address. Services with built-in rules stay on a Recommended shelf on top.
  Servers without them get generic rules: read-only tools run, the rest wait
  for you.
- **Approvals.** Writes to production, messages and other risky steps wait for
  you, in the app or in Telegram.
- **Plans first for features**, models and effort per role, and a spending cap
  per task that asks before going further.
- **Voice requests**, transcribed on your computer. The speech engine and model
  download the first time you use voice (about 160 MB, the size shown first),
  so the installer stays small for everyone who doesn't. For better accuracy
  with accents, names and other languages, choose Accuracy in Settings › Voice
  (a bigger download).
- **Updates** are downloaded in the background and offered with "Update ready,
  restart when you're done". Nothing restarts on its own, and updates never
  touch your data.
