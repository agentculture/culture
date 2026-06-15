"""Allow running culture as ``python -m culture``.

``culture.cli`` is aliased to ``culture_core.cli`` by the meta-path finder in
``culture/__init__.py``, so this delegates to the engine's CLI entry point — the
same target as the ``culture`` console script.
"""

from culture.cli import main

if __name__ == "__main__":
    main()
