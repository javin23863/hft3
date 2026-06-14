from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO = Path(__file__).resolve().parents[1]
_PKG = _REPO / "packages"
for _p in (_REPO, _PKG):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from scripts import pull_fixing_windows as script


def _window(day: str) -> dict:
    return {
        "date": day,
        "expiry_kinds": ["monthly"],
        "symbols": ["ES"],
        "start_utc": f"{day}T19:55:00+00:00",
        "end_utc": f"{day}T20:05:00+00:00",
    }


@pytest.fixture
def isolated_script(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    out_dir = tmp_path / "fixing_mbo"
    monkeypatch.setattr(script, "OUT_DIR", str(out_dir))
    monkeypatch.setattr(script, "LOG", str(out_dir / "pull_log.txt"))
    monkeypatch.setattr(script, "ALREADY_COVERED", set())
    monkeypatch.setattr(
        script,
        "plan_fixing_windows",
        lambda *_args, **_kwargs: [_window("2026-06-11"), _window("2026-06-12")],
    )
    return out_dir


def test_dry_run_default_does_not_instantiate_client_or_write_log(
    isolated_script: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client_cls = MagicMock()
    monkeypatch.setattr(script, "DatabentoResearchClient", client_cls)

    assert script.main([]) == 0

    client_cls.assert_not_called()
    assert not (isolated_script / "pull_log.txt").exists()
    out = capsys.readouterr().out
    assert "PLAN schema=trades missing=2 skipped=0" in out
    assert "PLAN 2026-06-12" in out


def test_single_date_filter_limits_planned_window(
    isolated_script: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(script, "DatabentoResearchClient", MagicMock())

    assert script.main(["--dry-run", "--date", "2026-06-12"]) == 0

    out = capsys.readouterr().out
    assert "PLAN schema=trades missing=1 skipped=0" in out
    assert "2026-06-12" in out
    assert "2026-06-11" not in out


def test_unknown_date_filter_fails_loudly(isolated_script: Path) -> None:
    with pytest.raises(SystemExit, match="no fixing window planned for 2026-06-13"):
        script.main(["--dry-run", "--date", "2026-06-13"])


def test_existing_file_is_skipped_without_client(
    isolated_script: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    isolated_script.mkdir(parents=True)
    (isolated_script / "ES_fixing_trades_2026-06-12.dbn.zst").write_bytes(b"owned")
    monkeypatch.setattr(
        script,
        "plan_fixing_windows",
        lambda *_args, **_kwargs: [_window("2026-06-12")],
    )
    client_cls = MagicMock()
    monkeypatch.setattr(script, "DatabentoResearchClient", client_cls)

    assert script.main(["--dry-run", "--date", "2026-06-12"]) == 0

    client_cls.assert_not_called()
    out = capsys.readouterr().out
    assert "PLAN schema=trades missing=0 skipped=1" in out
    assert "SKIP 2026-06-12 exists" in out


def test_estimate_cost_prices_only_missing_windows_without_download(
    isolated_script: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    isolated_script.mkdir(parents=True)
    (isolated_script / "ES_fixing_trades_2026-06-11.dbn.zst").write_bytes(b"owned")
    client = MagicMock()
    client.estimate_cost.return_value = 0.08
    client_cls = MagicMock(return_value=client)
    monkeypatch.setattr(script, "DatabentoResearchClient", client_cls)

    assert script.main(["--estimate-cost"]) == 0

    client_cls.assert_called_once_with()
    client.estimate_cost.assert_called_once()
    client.download_event_window.assert_not_called()
    assert not (isolated_script / "pull_log.txt").exists()
    out = capsys.readouterr().out
    assert "ESTIMATE 2026-06-12 $0.0800" in out
    assert "ESTIMATE_TOTAL windows=1 skipped=1 cost=$0.0800" in out


def test_estimate_cost_with_no_missing_windows_does_not_instantiate_client(
    isolated_script: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    isolated_script.mkdir(parents=True)
    (isolated_script / "ES_fixing_trades_2026-06-12.dbn.zst").write_bytes(b"owned")
    monkeypatch.setattr(
        script,
        "plan_fixing_windows",
        lambda *_args, **_kwargs: [_window("2026-06-12")],
    )
    client_cls = MagicMock()
    monkeypatch.setattr(script, "DatabentoResearchClient", client_cls)

    assert script.main(["--estimate-cost", "--date", "2026-06-12"]) == 0

    client_cls.assert_not_called()
    assert not (isolated_script / "pull_log.txt").exists()
    assert "ESTIMATE_TOTAL windows=0 skipped=1 cost=$0.0000" in capsys.readouterr().out


def test_download_passes_override_flag_and_output_path(
    isolated_script: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        script,
        "plan_fixing_windows",
        lambda *_args, **_kwargs: [_window("2026-06-12")],
    )
    client = MagicMock()
    client.budget._calculate_total_used.return_value = 0.0
    client_cls = MagicMock(return_value=client)
    monkeypatch.setattr(script, "DatabentoResearchClient", client_cls)

    assert script.main(["--download", "--override-operating-cap", "--date", "2026-06-12"]) == 0

    client.download_event_window.assert_called_once()
    kwargs = client.download_event_window.call_args.kwargs
    assert kwargs["override_operating_cap"] is True
    assert kwargs["output_path"] == str(
        isolated_script / "ES_fixing_trades_2026-06-12.dbn.zst"
    )
    assert kwargs["schema"] == "trades"
