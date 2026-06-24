from __future__ import annotations

import sys
import types
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))


def test_cpp_loader_prefers_active_build_dir(monkeypatch, tmp_path: Path) -> None:
    from features_engine.src.features._cpp_loader import _candidate_dirs

    repo = tmp_path / "repo"
    active_build = tmp_path / "cmake-build-lane"
    monkeypatch.setenv("HFT3_FEATURES_CPP_BUILD_DIR", str(active_build))

    candidates = list(_candidate_dirs(repo))

    assert candidates[:3] == [
        active_build.resolve(),
        (active_build / "Release").resolve(),
        (active_build / "Debug").resolve(),
    ]
    assert repo / "build" in candidates


def test_cpp_loader_rejects_stale_sys_module_when_active_build_dir_is_set(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import features_engine.src.features._cpp_loader as loader

    active_build = tmp_path / "active-build"
    stale_build = tmp_path / "stale-build"
    active_build.mkdir()
    stale_mod = types.SimpleNamespace(__file__=str(stale_build / "hft3_features_cpp.so"))

    monkeypatch.setenv("HFT3_FEATURES_CPP_BUILD_DIR", str(active_build))
    monkeypatch.setattr(loader, "_cached", stale_mod)
    monkeypatch.setattr(loader, "_searched", True)
    monkeypatch.setitem(sys.modules, loader._MODULE_NAME, stale_mod)

    assert loader.load_cpp_features() is None
    assert loader._MODULE_NAME not in sys.modules


def test_cpp_loader_override_load_failure_returns_none_without_fallback(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import features_engine.src.features._cpp_loader as loader

    active_build = tmp_path / "active-build"
    active_build.mkdir()
    corrupt_module = active_build / "hft3_features_cpp.so"
    corrupt_module.write_bytes(b"not a shared library")
    calls = []

    def fail_load(entry, repo):
        calls.append(entry)
        raise ImportError("synthetic corrupt extension")

    monkeypatch.setenv("HFT3_FEATURES_CPP_BUILD_DIR", str(active_build))
    monkeypatch.setattr(loader, "_cached", None)
    monkeypatch.setattr(loader, "_searched", False)
    monkeypatch.setattr(loader, "_load_cpp_module_from_path", fail_load)
    monkeypatch.delitem(sys.modules, loader._MODULE_NAME, raising=False)

    assert loader.load_cpp_features() is None
    assert calls == [corrupt_module]
    assert loader._searched is True
    assert loader._MODULE_NAME not in sys.modules


def test_cpp_loader_retries_when_active_build_dir_changes_after_miss(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import features_engine.src.features._cpp_loader as loader

    first_build = tmp_path / "first-build"
    second_build = tmp_path / "second-build"
    first_build.mkdir()
    second_build.mkdir()
    module_path = second_build / "hft3_features_cpp.so"
    module_path.write_bytes(b"placeholder")
    loaded_mod = types.SimpleNamespace(__file__=str(module_path))
    calls = []

    def fake_load(entry, repo):
        calls.append(entry)
        return loaded_mod

    monkeypatch.setattr(loader, "_cached", None)
    monkeypatch.setattr(loader, "_searched", False)
    monkeypatch.setattr(loader, "_searched_active_build", None)
    monkeypatch.setattr(loader, "_load_cpp_module_from_path", fake_load)
    monkeypatch.delitem(sys.modules, loader._MODULE_NAME, raising=False)

    monkeypatch.setenv("HFT3_FEATURES_CPP_BUILD_DIR", str(first_build))
    assert loader.load_cpp_features() is None

    monkeypatch.setenv("HFT3_FEATURES_CPP_BUILD_DIR", str(second_build))
    assert loader.load_cpp_features() is loaded_mod
    assert calls == [module_path]
