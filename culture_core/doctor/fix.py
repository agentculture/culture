"""Culture doctor fix actions for unregistered repos."""

from __future__ import annotations

from culture_core.config import add_to_manifest, load_culture_yaml
from culture_core.doctor.model import Finding


def register_unregistered(
    config_path: str, class2_findings: list[Finding]
) -> list[tuple[str, str]]:
    """Register each class-2 (unregistered on-disk) repo into the server.yaml at config_path.

    Reuses add_to_manifest (writes ONLY server.yaml; never the repo's culture.yaml).
    Idempotent: a suffix already in the manifest is skipped. Returns the (suffix, directory)
    pairs actually added. Empty findings -> no writes, returns [].
    """
    added: list[tuple[str, str]] = []
    for f in class2_findings:
        try:
            agents = load_culture_yaml(f.path)
        except Exception:
            continue
        for a in agents:
            try:
                add_to_manifest(config_path, a.suffix, f.path)
                added.append((a.suffix, f.path))
            except ValueError:
                continue  # already registered -> idempotent skip
    return added
