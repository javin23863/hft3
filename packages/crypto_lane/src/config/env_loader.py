"""Discover and load crypto lane credentials from standard env file paths."""

from __future__ import annotations



import os

import sys

from pathlib import Path



from crypto_lane.src.types import repo_root_from_lane



_ENV_FALLBACKS: dict[str, tuple[str, ...]] = {

    "HFT3_CRYPTO_B2_KEY_ID": ("CAE_B2_KEY_ID", "AWS_ACCESS_KEY_ID"),

    "HFT3_CRYPTO_B2_APP_KEY": ("CAE_B2_APP_KEY", "AWS_SECRET_ACCESS_KEY"),

    "HFT3_CRYPTO_B2_BUCKET": ("CAE_B2_BUCKET",),

    "HFT3_CRYPTO_B2_SOURCE_BUCKET": ("CAE_B2_SOURCE_BUCKET", "B2_BUCKET"),

    "HFT3_CRYPTO_B2_ENDPOINT": ("CAE_B2_ENDPOINT", "B2_ENDPOINT_URL"),

}



_LOADED_FILES: list[Path] = []





def _desk_env_module():

    root = repo_root_from_lane()

    if str(root) not in sys.path:

        sys.path.insert(0, str(root))

    import desk_env



    return desk_env





def repo_env_paths() -> list[Path]:

    root = repo_root_from_lane()

    paths: list[Path] = [

        root / ".env",

    ]

    desk = _desk_env_module()

    desk_btc = desk.resolve_btc_node_env_path(root)

    if desk_btc is not None:

        paths.append(desk_btc)

    else:

        paths.extend([root / ".btc-node.env", Path.home() / ".btc-node.env"])

    keys_path = desk.resolve_desk_keys_path()

    if keys_path is not None:

        paths.append(keys_path)



    for env_name in ("CRYPTO_KEYS_ENV", "MACRO_KEYS_ENV", "QXL_KEYS_ENV"):

        explicit = os.environ.get(env_name, "").strip()

        if explicit:

            paths.append(Path(explicit))



    return paths





def _apply_aliases() -> None:

    for primary, fallbacks in _ENV_FALLBACKS.items():

        if os.environ.get(primary):

            continue

        for fallback in fallbacks:

            val = os.environ.get(fallback)

            if val:

                os.environ[primary] = val

                break

    ep = os.environ.get("HFT3_CRYPTO_B2_ENDPOINT", "")

    if ep.startswith("https://s3.") or "backblazeb2.com" in ep and "/file/" not in ep:

        os.environ.pop("HFT3_CRYPTO_B2_ENDPOINT", None)





def _load_plain_env(path: Path) -> None:

    _desk_env_module().load_plain_env(path, override=False)





def ensure_crypto_env() -> list[Path]:

    """Load env files once; return paths that were found."""

    global _LOADED_FILES

    if _LOADED_FILES:

        _apply_aliases()

        return list(_LOADED_FILES)



    root = repo_root_from_lane()

    desk = _desk_env_module()



    dotenv_path = root / ".env"

    if dotenv_path.is_file():

        try:

            from dotenv import load_dotenv



            load_dotenv(dotenv_path, override=False)

        except ImportError:

            _load_plain_env(dotenv_path)

        _LOADED_FILES.append(dotenv_path)



    for path in desk.load_sibling_pointer_envs(root):

        if path not in _LOADED_FILES:

            _LOADED_FILES.append(path)



    keys_path = desk.resolve_desk_keys_path()

    if keys_path and keys_path.is_file():

        _load_plain_env(keys_path)

        if keys_path not in _LOADED_FILES:

            _LOADED_FILES.append(keys_path)



    for path in repo_env_paths():

        if path == dotenv_path or path == keys_path:

            continue

        if path.is_file():

            _load_plain_env(path)

            if path not in _LOADED_FILES:

                _LOADED_FILES.append(path)



    _apply_aliases()

    return list(_LOADED_FILES)





def require_env(*names: str, hint: str = ".env.example") -> dict[str, str]:

    ensure_crypto_env()

    missing = [n for n in names if not os.environ.get(n)]

    if missing:

        loaded = ", ".join(str(p) for p in _LOADED_FILES) or "(none)"

        raise RuntimeError(

            f"Missing env keys: {missing}. Copy {hint} and populate local gitignored files. "

            f"Loaded: {loaded}"

        )

    return {n: os.environ[n] for n in names}





def resolved_lake_config() -> dict[str, str]:

    """Effective B2 bucket/source after YAML defaults (not only raw env)."""

    from crypto_lane.src.config_loader import load_yaml

    from crypto_lane.src.ingest.gold_reader import resolve_gold_bucket



    ensure_crypto_env()

    cfg = load_yaml(repo_root_from_lane() / "packages/crypto_lane/config/lake_sources.yaml")

    bucket_env = str(cfg.get("write_bucket_env", "HFT3_CRYPTO_B2_BUCKET"))

    source_env = str(cfg.get("source_bucket_env", "HFT3_CRYPTO_B2_SOURCE_BUCKET"))

    desk = _desk_env_module()

    keys_path = desk.resolve_desk_keys_path()

    return {

        "write_bucket": resolve_gold_bucket("binance"),

        "write_bucket_env": bucket_env,

        "write_bucket_env_set": "set" if os.environ.get(bucket_env) else "default",

        "source_bucket": os.environ.get(

            source_env, str(cfg.get("source_bucket_default", "quant-x-datasets"))

        ),

        "source_bucket_env": source_env,

        "source_bucket_env_set": "set" if os.environ.get(source_env) else "default",

        "endpoint": os.environ.get("HFT3_CRYPTO_B2_ENDPOINT", "") or "(native b2sdk)",

        "desk_keys_env": str(keys_path) if keys_path else "(not found)",

        "qxl_keys_env": os.environ.get("QXL_KEYS_ENV", "") or "(unset)",

    }





def env_status(*names: str) -> dict[str, str]:

    ensure_crypto_env()

    out: dict[str, str] = {}

    for name in names:

        val = os.environ.get(name, "")

        out[name] = "set" if val else "missing"

    return out





def redacted_env_report() -> dict[str, object]:

    ensure_crypto_env()

    from crypto_lane.src.ml.challenger_deps import challenger_import_status

    root = repo_root_from_lane()

    desk = _desk_env_module()

    node_status = desk.read_btc_node_status(root)

    keys = [

        "HFT3_CRYPTO_B2_KEY_ID",

        "HFT3_CRYPTO_B2_APP_KEY",

        "HFT3_CRYPTO_B2_BUCKET",

        "HFT3_CRYPTO_B2_SOURCE_BUCKET",

        "HFT3_CRYPTO_B2_ENDPOINT",

        "BTC_RPC_URL",

        "BTC_RPC_USER",

        "BTC_RPC_PASS",

        "COINSTATS_API_KEY",

        "CRYPTOCOMPARE_API_KEY",

        "ETHERSCAN_API_KEY",

        "FRED_API_KEY",

    ]

    return {

        "loaded_files": [str(p) for p in _LOADED_FILES],

        "keys": env_status(*keys),

        "resolved": resolved_lake_config(),

        "challengers": challenger_import_status(),

        "btc_node_env_path": str(desk.resolve_btc_node_env_path(root) or ""),

        "btc_node_status_path": str(desk.resolve_btc_node_status_path(root) or ""),

        "btc_node_synced": (
            bool(node_status.get("synced")) and not bool(node_status.get("stale"))
            if node_status
            else None
        ),

        "btc_node_tip_height": node_status.get("tip_height") if node_status else None,

        "btc_node_status_stale": node_status.get("stale") if node_status else None,

        "btc_node_status_age_hours": node_status.get("status_age_hours") if node_status else None,

    }


