from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _v

try:
    __version__ = _v("culture")
except PackageNotFoundError:
    __version__ = "0.0.0-dev"
