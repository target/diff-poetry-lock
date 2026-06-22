from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("diff-poetry-lock")
except PackageNotFoundError:
    __version__ = "dev"
