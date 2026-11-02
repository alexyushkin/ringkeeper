# Add-on tools (MCP servers)

Add-on tools give agents abilities Ringkeeper doesn't have built in: a web
browser, a search engine, a service's own official tool. They're MCP servers;
Ringkeeper runs them behind its gateway, keeps their keys, and checks each call.

## Adding one

- **From the catalog** (Connections › Add, or the first run): search, then sign
  in or paste a key. Some run on this computer (Ringkeeper installs them).
- **From Claude Desktop:** Settings › From Claude Desktop imports the tools you
  already set up there; their keys move into Ringkeeper's vault.
- **By address:** a tool that isn't listed, by its `https://` address.

A tool that holds no client's data (a browser, your own files) can be a
**shared tool**: yours, linkable to any client's projects.

## What's held

Reading tools run. Everything else waits for your approval. Per tool, you can
set it to need the connection's name typed, or to run without asking.

## Undo

Ringkeeper can't undo what another tool's server does.
