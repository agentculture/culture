"""Hook scripts installed into ``~/.claude/settings.json`` by
``install.py``. Each script reads a JSON event from stdin and writes a
hook decision JSON to stdout. They are invoked as standalone Python
subprocesses by Claude Code, so they keep imports minimal.
"""
