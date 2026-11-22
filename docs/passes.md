# Passes

A pass lets an AI app on this computer use one project's connections: Claude Desktop, Claude
Code or Cursor. The app talks to Ringkeeper, never to the service, and every call is
checked the same way a task's is:

- It reaches only the project's own connections. Another client's connection is refused on
  every call, even if it was linked by mistake.
- A change to production waits for your approval in Ringkeeper, like any held change.
- Every change it makes, and every call that's refused, is in Activity under the pass's name,
  with secrets taken out.
- A change it made can be undone for 7 days, from the pass in Settings > Passes or from
  [Undo](undo.md).
- It can't use Claude through Ringkeeper, reach another project, or change Ringkeeper's settings.

The app is also told, when it connects, whose connections these are ("Acme's connections, for
the project Acme web") and to use them, and nothing else, for that client's data. An AI app
often has connectors of its own to the same services, like Notion, Airtable or Google. Those
are your own accounts, not the client's, so used for the client's work they'd read or change
the wrong one. Its `ringkeeper_about` tool says the same: the client, the project and the rule.

The app never sees the connections' keys, and the pass's own key stays in Ringkeeper's vault:
what you add to the app only starts Ringkeeper's bridge on this computer.

## Make a pass

1. Settings > Passes > New pass. Pick the project, and give the pass a name the app will list it
   by, like `acme-code`.
2. On the pass, click **Add to** the app:
   - **Claude Desktop:** the pass is saved into your Downloads folder as an extension,
     `Ringkeeper-<pass>.mcpb`, and opened, so Claude Desktop asks whether to install it. Click
     Install. If Claude doesn't ask, double-click the file, or add it from Claude's
     Customize > Connectors.
   - **Claude Code** and **Cursor:** Ringkeeper adds the pass to the app's own settings, for
     every folder you use the app in. An app that isn't on this computer says so.
3. Start a new chat (Claude Desktop), a new session (Claude Code), or restart Cursor.
   Ringkeeper has to be running while you use it.

The pass shows **Added** for each app it's in, and **Take it out** removes it from that app.
Removing a pass takes it out of Claude Code and Cursor too. Claude Desktop's extension is taken
out in Claude, under Settings > Extensions.

### What Ringkeeper changes in Claude Code and Cursor

Only the app's list of servers: `mcpServers` in Claude Code's `.claude.json` (in your home
folder, or in `CLAUDE_CONFIG_DIR` if you set one) and in Cursor's `.cursor/mcp.json`. Every
other setting stays as it was, and that's checked before anything is saved. Before each
change, an encrypted copy of the file as it was is kept in Ringkeeper's data folder, under
`app-settings-backups` (the newest five per app).

Ringkeeper leaves the file alone, and says why, when it isn't valid JSON (a comment in it, say),
when Claude Code hasn't saved its settings yet (open it once first), or when the app keeps
changing the file while Ringkeeper saves. Then **Add it by hand instead**, on the pass, has the
steps: a command for Claude Code, the JSON for Cursor.

**Added for an older version** means the app starts the pass the way an earlier install did.
Click **Update**.

The app then has tools named by service: `database_query`, `airtable_list_records`,
`memory_recall`, and so on, and Ringkeeper's own: `ringkeeper_ask`, `ringkeeper_task_status`,
`ringkeeper_check_approval` and `ringkeeper_about`.

The extension starts Ringkeeper from where it's installed (usually
`%LOCALAPPDATA%\Programs\Ringkeeper` on Windows), so it keeps working after Ringkeeper updates.

### Claude Desktop by hand

If the extension won't install, you can add the pass to Claude Desktop's settings file
yourself:

1. **Quit Claude completely first:** File > Exit on Windows (or right-click its icon by the
   clock and choose Quit), Claude > Quit Claude on a Mac. Closing the window isn't enough.
   Claude rewrites the file while it runs, and an edit made then is lost.
2. Open `claude_desktop_config.json`: `%APPDATA%\Claude\` on Windows,
   `~/Library/Application Support/Claude/` on a Mac. Copy it somewhere first.
3. In the pass, under Add it by hand instead > Claude Desktop, copy the JSON, and add its entry
   under `mcpServers`, beside any servers already there.
4. Start Claude.

## Hand Ringkeeper a whole request

Besides using the connections itself, the app can give Ringkeeper a request with
`ringkeeper_ask`, as if you'd typed it in Ringkeeper: "find last week's failed orders and
refund them". It becomes a task in the pass's project, and only there, with everything a task
has: its own conversation in Ringkeeper, the usual limits and spending cap, approvals for
changes to production, and the audit log, under the pass's name.

The app waits up to 50 seconds (Settings > Passes; AI apps commonly give up on a tool after about
60). If the task is done by then, it gets the result. If not, it gets the task's link and checks
later with `ringkeeper_task_status`. If the task needs your approval for a change, or the
client is over its monthly cap, the app gets the link to Approvals, as for a change it held
itself.

## When a change waits for approval

The app is told the change is waiting, and gets a link to Ringkeeper's Approvals to give you.
Open it and click **Approve** or **Deny** on the change. The link brings Ringkeeper's window up
at Approvals.

The app can ask what became of the change with its `ringkeeper_check_approval` tool: it gets the
same link while the change is waiting, and the result once it ran.

## Revoke or remove a pass

**Revoke** stops it: the app's next call is refused, and the refusal is in Activity. A revoked
pass still lists the apps it's in, with **Take it out**. **Remove** deletes a revoked pass and its
key, and takes it out of Claude Code and Cursor. What it did stays in Activity.

## When Ringkeeper quits, restarts or updates

The bridge stops when Ringkeeper does, so an update can replace Ringkeeper's files.

- **Claude Desktop** starts it again by itself once Ringkeeper is back. In the meantime it's told
  Ringkeeper isn't running and to try again in a minute.
- **Claude Code:** run `/mcp` and reconnect the pass.
- **Cursor:** turn the pass off and on again in Cursor's MCP settings.

**The app says Ringkeeper isn't running?** Start Ringkeeper. If the app was opened before
Ringkeeper, restart the app: it starts the bridge when it opens.

**It says there's no pass by that name?** It was removed, or the name in the app's settings is
different from the one in Settings > Passes.
