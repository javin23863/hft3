# HftBacktest vendor pin (HBT realism lane)

Official [nkaz001/hftbacktest](https://github.com/nkaz001/hftbacktest) PyPI package for VectorBT→HftBacktest handoff and `tests/backtest_pipeline/test_hftbacktest_realism_hbt*.py`.

Pin: [`VENDOR.lock`](VENDOR.lock)

Install pinned package (matches source-lock `upstream_ref` verification):

```bash
bash scripts/install_hftbacktest_realism_deps.sh
```

The installed `python_package_version` must match `upstream_commit_sha_or_tag` (`v2.4.2` ↔ `2.4.2`) for `upstream_ref_verification_status=package_version_match`.
