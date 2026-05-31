"""Backblaze B2 download client for crypto lane bronze."""
from __future__ import annotations

import io
import os
from pathlib import Path

from crypto_lane.src.config.env_loader import ensure_crypto_env, require_env


class B2ClientError(RuntimeError):
    pass


class B2Client:
    def __init__(self) -> None:
        ensure_crypto_env()
        creds = require_env("HFT3_CRYPTO_B2_KEY_ID", "HFT3_CRYPTO_B2_APP_KEY", hint=".env.example")
        try:
            from b2sdk.v2 import B2Api, InMemoryAccountInfo
        except ImportError as exc:
            raise B2ClientError("b2sdk required: pip install b2sdk") from exc

        self._api = B2Api(InMemoryAccountInfo())
        self._api.authorize_account("production", creds["HFT3_CRYPTO_B2_KEY_ID"], creds["HFT3_CRYPTO_B2_APP_KEY"])
        endpoint = os.environ.get("HFT3_CRYPTO_B2_ENDPOINT", "").strip()
        if endpoint:
            self._api.session.API_URL = endpoint.rstrip("/")

    def download_to_path(self, bucket_name: str, key: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        bucket = self._api.get_bucket_by_name(bucket_name)
        try:
            downloaded = bucket.download_file_by_name(key)
            downloaded.save_to(str(dest))
        except Exception as exc:
            raise B2ClientError(f"B2 download failed bucket={bucket_name} key={key}: {exc}") from exc
        return dest

    def download_bytes(self, bucket_name: str, key: str) -> bytes:
        bucket = self._api.get_bucket_by_name(bucket_name)
        buf = io.BytesIO()
        try:
            downloaded = bucket.download_file_by_name(key)
            downloaded.save(buf)
        except Exception as exc:
            raise B2ClientError(f"B2 download failed bucket={bucket_name} key={key}: {exc}") from exc
        return buf.getvalue()

    def file_exists(self, bucket_name: str, key: str) -> bool:
        bucket = self._api.get_bucket_by_name(bucket_name)
        try:
            bucket.get_file_info_by_name(key)
            return True
        except Exception:
            return False
