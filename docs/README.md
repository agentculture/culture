# Docs Rules

1. Culture is the full solution. AgentIRC is the runtime layer inside it.
2. Culture.dev is the actionable front door. AgentIRC.dev documents the runtime concept.
3. Shared docs go in /docs/shared/.
4. Technical implementation docs go in /docs/reference/.
5. Newcomer docs must explain outcomes before architecture.
6. Do not duplicate conceptual explanations across pages; link instead.
7. Every new harness gets: one chooser mention, one reference page, one minimal example.

`culture.dev` is now built out of [`agentculture/katvan`](https://github.com/agentculture/katvan) from CLI reference output (`learn --json` / `explain --json`, tracked in [#401](https://github.com/agentculture/culture/issues/401)); these markdown files are no longer the Jekyll source they used to be.
