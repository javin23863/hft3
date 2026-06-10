"""
Locate and import the hft3_features_cpp pybind11 extension module.

Usage
-----
    from features._cpp_loader import load_cpp_features

    cpp = load_cpp_features()
    if cpp is None:
        # fall back to pure-Python MBOFeatureExtractor
        ...
    else:
        fe = cpp.FeatureExtractor(tick_size=0.25)

Search order (mirrors apps/workbench/src/sim/cpp_binary.py):
  1. Already imported (cached in sys.modules).
  2. build/          (flat — most CMake single-config generators)
  3. build/Release/  (multi-config, Release)
  4. build/Debug/    (multi-config, Debug)
  5. Anything on sys.path (e.g. if installed via pip install -e .)

The module is never None when it is found; returns None only when the shared
library has not been built yet so that callers can fall back gracefully.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
import warnings
from pathlib import Path
from typing import Optional

_MODULE_NAME = "hft3_features_cpp"

# Cache: once found, keep the reference.
_cached: Optional[object] = None
_searched: bool = False


def _repo_root() -> Path:
    """Return repo root (two packages/ levels up from this file)."""
    # this file: packages/features_engine/src/features/_cpp_loader.py
    return Path(__file__).resolve().parents[4]


def _candidate_dirs(repo: Path):
    """Yield directories to search for the .pyd / .so file."""
    build = repo / "build"
    yield build
    yield build / "Release"
    yield build / "Debug"


def _check_staleness(pyd_path: Path, repo: Path) -> None:
    """Warn (do NOT fail) if any C++ source is newer than the .pyd.

    Scans packages/features_engine/cpp/{src,include,bindings}/*.{cpp,hpp}.
    Emits a single warnings.warn if any source mtime > pyd mtime.
    """
    try:
        pyd_mtime = pyd_path.stat().st_mtime
    except OSError:
        return  # pyd disappeared between find and stat — ignore

    cpp_root = repo / "packages" / "features_engine" / "cpp"
    glob_patterns = [
        "src/*.cpp", "src/*.hpp",
        "include/*.cpp", "include/*.hpp",
        "bindings/*.cpp", "bindings/*.hpp",
    ]
    newest_src: Optional[Path] = None
    newest_mtime: float = 0.0
    for pattern in glob_patterns:
        for src in cpp_root.glob(pattern):
            try:
                mt = src.stat().st_mtime
            except OSError:
                continue
            if mt > newest_mtime:
                newest_mtime = mt
                newest_src = src

    if newest_src is not None and newest_mtime > pyd_mtime:
        warnings.warn(
            f"hft3_features_cpp.pyd may be stale: {newest_src.name} is newer than "
            f"{pyd_path.name}. Run 'cmake --build build' to rebuild.",
            stacklevel=4,
        )


def load_cpp_features() -> Optional[object]:
    """
    Import and return the hft3_features_cpp module, or None if not built.

    Thread-safe for read-after-first-call; the first call is not guaranteed
    reentrant but that is acceptable for a module-level loader.
    """
    global _cached, _searched
    if _searched:
        return _cached

    # 1. Already in sys.modules (e.g. from a prior import or pip-install).
    mod = sys.modules.get(_MODULE_NAME)
    if mod is not None:
        _cached = mod
        _searched = True
        return _cached

    # 2. Try sys.path first — covers editable installs.
    try:
        _cached = importlib.import_module(_MODULE_NAME)
        _searched = True
        return _cached
    except ImportError:
        pass

    # 3. Search build directories.
    repo = _repo_root()
    for search_dir in _candidate_dirs(repo):
        if not search_dir.is_dir():
            continue
        for entry in search_dir.iterdir():
            # Match hft3_features_cpp*.pyd  or  hft3_features_cpp*.so
            if entry.name.startswith(_MODULE_NAME) and entry.suffix in (".pyd", ".so"):
                spec = importlib.util.spec_from_file_location(_MODULE_NAME, entry)
                if spec is None or spec.loader is None:
                    continue
                try:
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules[_MODULE_NAME] = mod
                    spec.loader.exec_module(mod)  # type: ignore[union-attr]
                    _cached = mod
                    _searched = True
                    _check_staleness(entry, repo)
                    return _cached
                except Exception:
                    # Clean up on failure so a later retry can try the next candidate.
                    sys.modules.pop(_MODULE_NAME, None)
                    continue

    _searched = True
    return None
