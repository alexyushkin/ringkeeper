"""The `ringkeeper` command line.

Commands parse input into schemas and call the services layer. They never
write to the database themselves, so they can later be pointed at the API
without the rules moving.
"""
