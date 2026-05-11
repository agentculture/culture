<!-- markdownlint-disable MD041 -->
<!-- This file is a template rendered by announce-skill-update.sh. -->
<!-- It posts as an issue body where GitHub supplies the title, so -->
<!-- there's no H1 here on purpose. Placeholders use `{{NAME}}` syntax. -->

## What's stale

Your repo's `.claude/skills/{{SKILL}}/` (or its older vendored
name — see your `docs/skill-sources.md` if you keep a provenance
ledger) is a vendored copy of the steward skill that has since
moved on. Relevant CHANGELOG entries from steward:

{{CHANGELOG_BLOCK}}

## Cite locations (source of truth)

- Local sibling checkout (preferred when available):
  `../steward/.claude/skills/{{SKILL}}/`
- Remote, if the workspace doesn't include a local steward checkout:
  <https://github.com/agentculture/steward/tree/main/.claude/skills/{{SKILL}}>

The directory ships with a `SKILL.md` and a `scripts/` directory
per the AgentCulture skills-portability rule (each skill is
self-contained; nothing reaches across skill boundaries at
runtime).

## What's in the upstream now

`{{SKILL}}/scripts/` ({{UPSTREAM_SCRIPT_COUNT}} files):

{{UPSTREAM_SCRIPT_LIST}}

{{DELTA_BLOCK}}

{{NOTE_BLOCK}}

## What to do

```bash
# 0. Branch.
git checkout -b skill/{{SKILL}}-resync

# 1. Replace your vendored copy with the current upstream.
#    If your old vendored name differs (e.g. pr-review → cicd),
#    `git rm` the old dir first.
git rm -r .claude/skills/<old-name-if-different>   # skip if name is already {{SKILL}}
cp -R ../steward/.claude/skills/{{SKILL}} .claude/skills/
chmod +x .claude/skills/{{SKILL}}/scripts/*.sh 2>/dev/null || true

# 2. Adapt identifiers per the existing "identifier-only adapted"
#    pattern in your docs/skill-sources.md (if you keep one):
#    - SKILL.md prose framing: replace "steward" with your repo
#      name where it identifies the consumer (NOT where it cites
#      steward as the upstream).
#    - For any script that hard-codes a signature literal, change it
#      to `- <your-repo> (Claude)`. (The communicate skill no longer
#      hard-codes one — agtag resolves the nick from your local
#      `culture.yaml`. If your repo is missing one, add it or pass
#      `--as <your-repo>` at the call site.)

# 3. Update docs/skill-sources.md (if present) to point at the
#    new path; drop any stale row for the old name.

# 4. Sweep for stale references:
grep -rn '<old-name-if-different>' .claude docs CLAUDE.md README.md 2>/dev/null
#    Replace any survivors with `{{SKILL}}`.

# 5. Bump version per project convention (CI version-check
#    enforces in AgentCulture siblings); add CHANGELOG entry.
```

## Acceptance criteria

- `.claude/skills/{{SKILL}}/SKILL.md` is present with frontmatter
  `name: {{SKILL}}`.
- `.claude/skills/{{SKILL}}/scripts/` contains the
  {{UPSTREAM_SCRIPT_COUNT}} files listed above (and only those
  files, unless your repo intentionally adds extras — record
  divergence in the SKILL.md frontmatter `description` per the
  AgentCulture vendoring policy).
- All scripts are executable (`chmod +x`).
- If the skill hard-codes a signature literal anywhere, your
  vendored copy uses your repo's signature, not `- steward (Claude)`.
- If you keep a `docs/skill-sources.md`, it lists `{{SKILL}}` with
  the upstream `../steward/.claude/skills/{{SKILL}}/`; no row
  remains for the old vendored name.
- `grep -rn '<old-name-if-different>' .claude docs CLAUDE.md README.md`
  returns zero hits, or only historical mentions in CHANGELOG.
- `CHANGELOG.md` has a new version entry describing the resync;
  the version is bumped per project convention; CI's
  `version-check` job is green.

## References

- Steward CHANGELOG (release-by-release deltas):
  <https://github.com/agentculture/steward/blob/main/CHANGELOG.md>
- `{{SKILL}}` SKILL.md:
  <https://github.com/agentculture/steward/blob/main/.claude/skills/{{SKILL}}/SKILL.md>
- AgentCulture skills-portability rule (why each vendor adapts
  signature literals locally): the `communicate` SKILL.md
  "Per-channel signature rules" + "Conventions in use" sections.
