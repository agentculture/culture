from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _v

# The engine ships in the `culture` distribution since the 14.0.0 merge-back
# (culture#462) — the standalone `culture-core` dist is retired, so the version
# is the front-door's.
try:
    __version__ = _v("culture")
except PackageNotFoundError:
    __version__ = "0.0.0-dev"
