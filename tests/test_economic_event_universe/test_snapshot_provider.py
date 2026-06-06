from unittest.mock import MagicMock, patch

import pandas as pd

from economic_event_universe.snapshot import DefaultSnapshotProvider


def test_snapshot_provider_collect_offset(tmp_path):
    repo = tmp_path
    ds = repo / "packages" / "data_system" / "config"
    ds.mkdir(parents=True)
    (ds / "events.csv").write_text(
        "event_id,event_type,release_date,release_time,timezone,window_name,"
        "start_offset_seconds,end_offset_seconds,symbols,priority,source,source_url,effective_date,notes\n"
        "CPI_2024_09_11_TIGHT,CPI,2024-09-11,08:30:00,America/New_York,TIGHT,-60,10,"
        "MES.v.0,1,BLS,https://bls.gov,2018-01-01,test\n",
        encoding="utf-8",
    )
    fake_df = pd.DataFrame([{"offset_sec": 0, "symbol": "MES.v.0", "mid_price": 1.0}])
    with patch("economic_event_universe.snapshot.build_l3_event_tensor", return_value=fake_df):
        with patch("economic_event_universe.snapshot.resolve_npz_for_event", return_value=(None, False, "MES.v.0")):
            with patch("economic_event_universe.snapshot.data_system_root", return_value=ds.parent):
                with patch("economic_event_universe.snapshot.repo_root", return_value=repo):
                    p = DefaultSnapshotProvider(repo)
                    frame = p.collect("CPI_2024_09_11_TIGHT", 0, ["MES.v.0"])
    assert frame.offset_sec == 0
    assert frame.metadata["mbo_missing"] == ["MES.v.0"]
