"""The Ringkeeper worker: runs one Claude Agent SDK session in /workspace.

It is deliberately separate from the `ringkeeper` package: the worker image
carries no vault, key or registry code at all. It talks to the outside only
through stdout (JSON lines) and the gateway's MCP endpoints.
"""
