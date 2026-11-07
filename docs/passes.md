# Passes

A pass lets an AI app on this computer use one project's connections: Claude Code, Cursor,
or (soon) Claude Desktop. The app talks to Ringkeeper, never to the service, and every call is
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
what you paste into the app only starts Ringkeeper's bridge on this computer.

## Make a pass

1. Settings > Passes > New pass. Pick the project, and give the pass a name the app will list it
   by, like `acme-code`.
2. Choose the app, copy what it shows, and paste it:
   - **Claude Code:** run the command in a terminal. It adds the pass for every folder you use
     Claude Code in.
   - **Cursor:** put the JSON in `.cursor/mcp.json` in your home folder, or in one project's
     folder for that project only. If the file already lists servers, add this one beside them.
3. Restart the app. Ringkeeper has to be running while you use it.

The app then has tools named by service: `database_query`, `airtable_list_records`,
`memory_recall`, and so on.

## When a change waits for approval

The app is told the change is waiting, and is given its ID. Approve or deny it in Ringkeeper as
usual. The app can ask what became of it with its `ringkeeper_check_approval` tool, and gets the
result once it ran.

## Revoke or remove a pass

**Revoke** stops it: the app's next call is refused, and the refusal is in Activity. **Remove**
deletes a revoked pass and its key. What it did stays in Activity.

**The app says Ringkeeper isn't running?** Start Ringkeeper, then restart the app: it starts
the bridge only when it opens.

**It says there's no pass by that name?** It was removed, or the name in the app's settings is
different from the one in Settings > Passes.
