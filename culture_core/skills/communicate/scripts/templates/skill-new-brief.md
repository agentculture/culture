<!-- markdownlint-disable MD041 -->
<!-- This file is the NEW-SKILL template rendered by announce-skill-update -->
<!-- when --new is passed. It posts as an issue body where GitHub supplies -->
<!-- the title, so there's no H1 here on purpose. Placeholders use the -->
<!-- `{{NAME}}` syntax; {{ORIGIN_BLOCK}} is empty unless --origin is given. -->

## A new skill is available to vendor

`{{SKILL}}` is a **new** skill in the AgentCulture mesh. Your repo does
not have it yet — this brief tells you how to **add it fresh** (there is no
older vendored copy to replace). If you maintain a `docs/skill-sources.md`
provenance ledger, add a row for it; if not, just drop it into
`.claude/skills/`.

{{ORIGIN_BLOCK}}

Relevant guildmaster CHANGELOG entries (where guildmaster picked the skill up):

{{CHANGELOG_BLOCK}}

## Cite locations (source of truth)

- Local sibling checkout (preferred when available):
  `../guildmaster/.claude/skills/{{SKILL}}/`
- Remote, if the workspace doesn't include a local guildmaster checkout:
  <https://github.com/agentculture/guildmaster/tree/main/.claude/skills/{{SKILL}}>

guildmaster re-broadcasts this skill to the mesh, so its copy is the citation
point even when the skill originates in another sibling (see origin note
above, if present). The directory ships a `SKILL.md` and a `scripts/`
directory per the AgentCulture skills-portability rule (each skill is
self-contained; nothing reaches across skill boundaries at runtime).

## What's in the upstream now

`{{SKILL}}/scripts/` ({{UPSTREAM_SCRIPT_COUNT}} files):

{{UPSTREAM_SCRIPT_LIST}}

{{DELTA_BLOCK}}

{{NOTE_BLOCK}}

## What to do

```bash
# 0. Branch.
git checkout -b skill/{{SKILL}}-add

# 1. Add the skill fresh from upstream (no existing copy to remove).
cp -R ../guildmaster/.claude/skills/{{SKILL}} .claude/skills/
chmod +x .claude/skills/{{SKILL}}/scripts/*.sh 2>/dev/null || true

# 2. If you keep docs/skill-sources.md, add a row pointing at
#    ../guildmaster/.claude/skills/{{SKILL}}/ (record any local divergence).

# 3. Bump version per project convention (CI version-check enforces in
#    AgentCulture siblings); add a CHANGELOG entry.
```

## Acceptance criteria

- `.claude/skills/{{SKILL}}/SKILL.md` is present with frontmatter
  `name: {{SKILL}}`. **On the culture/agex backend, also add
  `type: command`** — `core.skill_loader` requires all of `name`,
  `description`, and `type:`, and a SKILL.md lacking `type:` is silently
  skipped by `backends/claude_code/probe.py`.
- `.claude/skills/{{SKILL}}/scripts/` contains the
  {{UPSTREAM_SCRIPT_COUNT}} files listed above (and only those, unless your
  repo intentionally adds extras — record divergence in the SKILL.md
  frontmatter `description` per the AgentCulture vendoring policy).
- All scripts are executable (`chmod +x`).
- If you keep a `docs/skill-sources.md`, it lists `{{SKILL}}` with the
  upstream `../guildmaster/.claude/skills/{{SKILL}}/`.
- `CHANGELOG.md` has a new version entry describing the addition; the
  version is bumped per project convention; CI's `version-check` job is green.

## References

- guildmaster CHANGELOG (release-by-release deltas):
  <https://github.com/agentculture/guildmaster/blob/main/CHANGELOG.md>
- `{{SKILL}}` SKILL.md:
  <https://github.com/agentculture/guildmaster/blob/main/.claude/skills/{{SKILL}}/SKILL.md>
- AgentCulture skills-portability rule (each vendor owns and may adapt its
  copy): the `communicate` SKILL.md "Conventions in use" section.
