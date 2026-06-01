"""Backblaze B2 download client for crypto lane bronze."""
from __future__ import annotations

import io
import os
import tempfile
import time
from pathlib import Path

from crypto_lane.src.config.env_loader import ensure_crypto_env, require_env


class B2ClientError(RuntimeError):
    pass


_RETRYABLE_EXC = (B2ClientError, ConnectionError, TimeoutError, OSError)


def _with_retry(fn, attempts: int = 3, backoff: tuple[float, ...] = (1.0, 2.0, 4.0)):
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except _RETRYABLE_EXC as exc:
            last_exc = exc
            if i == attempts - 1:
                raise
            time.sleep(backoff[i])
    if last_exc is not None:
        raise last_exc
    return None  # unreachable


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

        def _do_download() -> Path:
            fd, tmp_path = tempfile.mkstemp(prefix=dest.name + ".", dir=str(dest.parent))
            os.close(fd)
            try:
                downloaded = bucket.download_file_by_name(key)
                downloaded.save_to(tmp_path)
                os.replace(tmp_path, dest)
            except BaseException:
                # Cleanup on any failure (incl. cancellation). Use BaseException
                # so we still unlink the temp file on KeyboardInterrupt.
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
            return dest

        try:
            return _with_retry(_do_download)
        except _RETRYABLE_EXC as exc:
            raise B2ClientError(f"B2 download failed bucket={bucket_name} key={key}: {exc}") from exc
        except Exception as exc:
            # Re-wrap b2sdk business errors (FileNotPresent, etc.) but let
            # programmer errors (KeyError, AttributeError) propagate so the
            # caller sees the real class. The retry inside _with_retry
            # already skipped non-retryable errors.
            if exc.__class__.__module__.startswith("b2sdk"):
                raise B2ClientError(f"B2 download failed bucket={bucket_name} key={key}: {exc}") from exc
            raise

    def download_bytes(self, bucket_name: str, key: str) -> bytes:
        bucket = self._api.get_bucket_by_name(bucket_name)

        def _do_download() -> bytes:
            buf = io.BytesIO()
            downloaded = bucket.download_file_by_name(key)
            downloaded.save(buf)
            return buf.getvalue()

        try:
            return _with_retry(_do_download)
        except _RETRYABLE_EXC as exc:
            raise B2ClientError(f"B2 download failed bucket={bucket_name} key={key}: {exc}") from exc
        except Exception as exc:
            if exc.__class__.__module__.startswith("b2sdk"):
                raise B2ClientError(f"B2 download failed bucket={bucket_name} key={key}: {exc}") from exc
            raise

    def file_exists(self, bucket_name: str, key: str) -> bool:
        bucket = self._api.get_bucket_by_name(bucket_name)
        try:
            bucket.get_file_info_by_name(key)
            return True
        except (B2ClientError, ConnectionError, TimeoutError, OSError):
            return False
