# Add-on tools (MCP servers)

Add-on tools give agents abilities Ringkeeper doesn't have built in: a web
browser, a search engine, a service's own official tool. They're MCP servers;
Ringkeeper runs them behind its gateway, keeps their keys, and checks each call.

## Adding one

- **From the catalog** (Connectors › Browse connectors, or the first run): search, then sign
  in or paste a key. Some run on this computer (Ringkeeper installs them).
- **From your AI apps:** Settings › From your AI apps imports the tools you already set up
  in Claude Desktop, Claude Code, Cursor, VS Code, Windsurf, Gemini CLI, Cline, Roo Code,
  Zed, Continue, Codex, Kiro, Amazon Q and others. Each is listed with its app, and their
  keys move into Ringkeeper's vault. Ringkeeper only reads those apps' settings; it never
  changes them. A value written as a variable (`${env:NAME}`) is filled in from this
  computer; one that isn't set here is named, and that tool can't be imported until it is.
  A file Ringkeeper can't read (one with a mistake in it, say) is named too.
- **By address:** a tool that isn't listed, by its `https://` address.

A tool that holds no client's data (a browser, your own files) can be a
**shared tool**: yours, linkable to any client's projects.

## What's held

Reading tools run. Everything else waits for your approval. Per tool, you can
set it to need the connection's name typed, or to run without asking.

## Undo

Ringkeeper can't undo what another tool's server does.
