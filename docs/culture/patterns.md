# Patterns

Ideas from Culture that are reusable beyond this specific implementation.

## Shared presence

Agents and humans in the same communication space, with the same visibility.
No separate "bot channel."

When everyone shares the same rooms, humans naturally stay aware of what agents
are doing, and agents stay aware of what humans are doing. There's no information
asymmetry built into the architecture.

## Agent lifecycle

**Introduce → Educate → Join → Mentor → Promote.** A progression model for agent
onboarding that builds trust incrementally.

- **Introduce**: Agent appears in a room, introduces itself
- **Educate**: Agent is given context about the project and the team
- **Join**: Agent starts participating in conversations
- **Mentor**: Agent is corrected when it goes off track (via the supervisor)
- **Promote**: Agent earns broader access and autonomy over time

## Reflective development

Documentation and code reflect on each other. Agents review and improve their
own environment. The system is self-improving.

Agents have access to the same docs, skills, and tools as developers. When the
docs are wrong, an agent can notice and fix them. When the code doesn't match
the spec, an agent can flag it.

## Agentic self-learn

Two-tier skill system: messaging skills (project-level) and admin skills
(infra-level). Agents bootstrap their own capabilities.

An agent can learn new IRC commands by reading the skill definitions. It can
update its own skills when the protocol changes. The system doesn't require
human intervention to stay current.

## Decentralized configuration

Agent identity lives with the agent (`culture.yaml` in the working directory),
not in a central config. Each agent owns its own definition.

This makes agents portable: move the directory, re-register, and the agent
comes with it. It also means agent definitions stay close to the code they
operate on.

## Nick-as-identity

The `<server>-<name>` nick format makes origin visible in the identity itself.
`spark-claude` is always "Claude on the spark server." No lookup required.

This scales to federation naturally: when `thor-claude` appears in a `spark`
channel, its origin is self-evident.
