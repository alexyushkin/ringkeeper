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

The app never sees the connections' keys, and the pass's own key stays in Ringkeeper's vault:
what you add to the app only starts Ringkeeper's bridge on this computer.

## Make a pass

1. Settings > Passes > New pass. Pick the project, and give the pass a name the app will list it
   by, like `acme-code`.
2. Choose the app:
   - **Claude Desktop:** click **Save the extension**. It's saved into your Downloads folder as
     `Ringkeeper-<pass>.mcpb`. Double-click it, and Claude Desktop asks whether to install it.
     You can also add it from Claude's Customize > Connectors.
   - **Claude Code:** run the command in a terminal. It adds the pass for every folder you use
     Claude Code in.
   - **Cursor:** put the JSON in `.cursor/mcp.json` in your home folder, or in one project's
     folder for that project only. If the file already lists servers, add this one beside them.
3. Start a new chat (Claude Desktop), or restart the app (Claude Code, Cursor). Ringkeeper has
   to be running while you use it.

The app then has tools named by service: `database_query`, `airtable_list_records`,
`memory_recall`, and so on.

### Claude Desktop by hand

If the extension won't install, you can add the pass to Claude Desktop's settings file
yourself:

1. **Quit Claude completely first:** File > Exit on Windows (or right-click its icon by the
   clock and choose Quit), Claude > Quit Claude on a Mac. Closing the window isn't enough.
   Claude rewrites the file while it runs, and an edit made then is lost.
2. Open `claude_desktop_config.json`: `%APPDATA%\Claude\` on Windows,
   `~/Library/Application Support/Claude/` on a Mac. Copy it somewhere first.
3. In the pass, under Claude Desktop > Add it by hand instead, copy the JSON, and add its entry
   under `mcpServers`, beside any servers already there.
4. Start Claude.

## When a change waits for approval

The app is told the change is waiting, and gets a link to Ringkeeper's Approvals to give you.
Open it and click **Approve** or **Deny** on the change. The link brings Ringkeeper's window up
at Approvals.

The app can ask what became of the change with its `ringkeeper_check_approval` tool: it gets the
same link while the change is waiting, and the result once it ran.

## Revoke or remove a pass

**Revoke** stops it: the app's next call is refused, and the refusal is in Activity. **Remove**
deletes a revoked pass and its key. What it did stays in Activity.

**The app says Ringkeeper isn't running?** Start Ringkeeper, then restart the app: it starts
the bridge only when it opens.

**It says there's no pass by that name?** It was removed, or the name in the app's settings is
different from the one in Settings > Passes.
