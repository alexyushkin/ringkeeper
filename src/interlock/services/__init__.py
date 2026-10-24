"""The services layer: every registry write goes through here.

The CLI (and later the API) only call these functions. They never touch the
ORM to write, so the rules live in exactly one place.
"""
