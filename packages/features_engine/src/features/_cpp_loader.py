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
  2. HFT3_FEATURES_CPP_BUILD_DIR, when set by a lane runner.
  3. Anything on sys.path (e.g. if installed via pip install -e .)
  4. build/          (flat - most CMake single-config generators)
  5. build/Release/  (multi-config, Release)
  6. build/Debug/    (multi-config, Debug)

The module is never None when it is found; returns None only when the shared
library has not been built yet so that callers can fall back gracefully.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import warnings
from pathlib import Path
from typing import Optional

_MODULE_NAME = "hft3_features_cpp"

# Cache: once found, keep the reference.
_cached: Optional[object] = None
_searched: bool = False
_searched_active_build: Optional[Path] = None


def _repo_root() -> Path:
    """Return repo root (two packages/ levels up from this file)."""
    # this file: packages/features_engine/src/features/_cpp_loader.py
    return Path(__file__).resolve().parents[4]


def _deduped_dirs(paths):
    seen: set[Path] = set()

    for path in paths:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            yield resolved


def _override_candidate_dirs():
    """Yield active CMake build directories requested by a lane runner."""
    override = os.environ.get("HFT3_FEATURES_CPP_BUILD_DIR")
    if not override:
        return
    override_path = Path(override)
    yield from _deduped_dirs((override_path, override_path / "Release", override_path / "Debug"))


def _candidate_dirs(repo: Path):
    """Yield default fallback dirs; env override is handled first in load_cpp_features()."""
    build = repo / "build"
    yield from _deduped_dirs((build, build / "Release", build / "Debug"))


def _active_build_dir() -> Optional[Path]:
    override = os.environ.get("HFT3_FEATURES_CPP_BUILD_DIR")
    if not override:
        return None
    return Path(override).resolve()


def _module_file_under_build_dir(mod: object, build_dir: Path) -> bool:
    module_file = getattr(mod, "__file__", None)
    if not module_file:
        return False
    try:
        Path(module_file).resolve().relative_to(build_dir)
        return True
    except (OSError, ValueError):
        return False


def _load_cpp_module_from_path(entry: Path, repo: Path) -> Optional[object]:
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, entry)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = mod
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except Exception:
        sys.modules.pop(_MODULE_NAME, None)
        raise
    _check_staleness(entry, repo)
    return mod


def _iter_extension_candidates(search_dir: Path):
    for entry in search_dir.iterdir():
        if entry.name.startswith(_MODULE_NAME) and entry.suffix in (".pyd", ".so"):
            yield entry


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
    global _cached, _searched, _searched_active_build
    active_build = _active_build_dir()
    if _searched:
        if active_build != _searched_active_build:
            _cached = None
            _searched = False
            sys.modules.pop(_MODULE_NAME, None)
        elif active_build is not None and _cached is not None and not _module_file_under_build_dir(_cached, active_build):
            _cached = None
            _searched = False
            sys.modules.pop(_MODULE_NAME, None)
        else:
            return _cached

    # 1. Already in sys.modules (e.g. from a prior import or pip-install).
    mod = sys.modules.get(_MODULE_NAME)
    if mod is not None:
        if active_build is not None and not _module_file_under_build_dir(mod, active_build):
            sys.modules.pop(_MODULE_NAME, None)
        else:
            _cached = mod
            _searched = True
            _searched_active_build = active_build
            return _cached

    # 2. Search the requested build dir first. In lane-runner mode, a broken
    # artifact must not fall through to a stale default build or site package.
    repo = _repo_root()
    for search_dir in _override_candidate_dirs():
        if not search_dir.is_dir():
            continue
        for entry in _iter_extension_candidates(search_dir):
            try:
                _cached = _load_cpp_module_from_path(entry, repo)
            except Exception:
                sys.modules.pop(_MODULE_NAME, None)
                _cached = None
                _searched = True
                _searched_active_build = active_build
                return None
            _searched = True
            _searched_active_build = active_build
            return _cached

    if os.environ.get("HFT3_FEATURES_CPP_BUILD_DIR"):
        _searched = True
        _searched_active_build = active_build
        return None

    # 3. Try sys.path before default build dirs so editable installs are not
    # shadowed by stale local CMake artifacts.
    try:
        _cached = importlib.import_module(_MODULE_NAME)
        _searched = True
        _searched_active_build = active_build
        return _cached
    except ImportError:
        pass

    for search_dir in _candidate_dirs(repo):
        if not search_dir.is_dir():
            continue
        for entry in _iter_extension_candidates(search_dir):
            try:
                _cached = _load_cpp_module_from_path(entry, repo)
                _searched = True
                _searched_active_build = active_build
                return _cached
            except Exception:
                sys.modules.pop(_MODULE_NAME, None)
                continue

    _searched = True
    _searched_active_build = active_build
    return None
