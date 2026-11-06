"""Bounded object I/O and FFmpeg finalization. No user text enters a shell."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import unicodedata
from fractions import Fraction
from pathlib import Path
from typing import Any

from minio.commonconfig import CopySource

from tts_erp_v2.publishing.object_store import MinioVideoStore
from tts_erp_v2.storage.minio_client import MinioClient


class MediaError(RuntimeError):
    pass


def component(value: str) -> str:
    value = unicodedata.normalize("NFC", value).strip()
    value = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", value).strip(". ")[:90]
    if not value or value in {".", ".."}:
        raise MediaError("INVALID_EXPORT_NAME")
    return value


def archive_path(inputs: dict) -> str:
    style, sku, human = (
        component(inputs[k]) for k in ("style", "skuLabel", "humanName")
    )
    template = inputs["templateName"]
    return f"成果/{style}/模板{template}/{human}/{sku}/{style}_{sku}_{human}_{template}{inputs['clip']:02d}.mp4"


def sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


class ObjectStore:
    def __init__(self, client: MinioClient | None = None) -> None:
        self.client = client or MinioClient.from_env().with_bucket(
            os.environ.get("RUNNINGHUB_MINIO_BUCKET", "tts-erp-runninghub")
        )

    @property
    def bucket(self) -> str:
        return self.client.bucket

    def freeze(self, source: dict, key: str) -> dict:
        sdk = self.client._sdk
        self.client.ensure_bucket()
        original = sdk.stat_object(source["bucket"], source["objectKey"])
        if original.size != source["sizeBytes"]:
            raise MediaError("SOURCE_SIZE_CHANGED")
        sdk.copy_object(
            self.bucket,
            key,
            CopySource(source["bucket"], source["objectKey"], match_etag=original.etag),
        )
        frozen = self.client.stat(key)
        return {
            **source,
            "bucket": self.bucket,
            "objectKey": key,
            "etag": frozen["etag"],
        }

    def fetch(self, source: dict, destination: Path) -> str:
        bound = self.client.with_bucket(source["bucket"])
        return MinioVideoStore(bound).download(
            source["objectKey"], destination, source["etag"]
        )

    def put(self, path: Path, key: str) -> dict:
        self.client.ensure_bucket()
        self.client._sdk.fput_object(
            self.bucket, key, str(path), content_type="video/mp4"
        )
        return {"bucket": self.bucket, "objectKey": key, "sha256": sha256(path)}

    def url(self, bucket: str, key: str) -> str:
        return self.client.with_bucket(bucket).presign_get(key)[0]

    def fetch_artifact(self, artifact: Any, destination: Path) -> None:
        bound = self.client.with_bucket(artifact.bucket)
        stat = bound.stat(artifact.object_key)
        MinioVideoStore(bound).download(artifact.object_key, destination, stat["etag"])
        if sha256(destination) != artifact.sha256:
            raise MediaError("ARTIFACT_HASH_MISMATCH")


def _run(args: list[str]) -> str:
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, check=True, timeout=300
        )
        return result.stdout
    except (OSError, subprocess.SubprocessError):
        # FFmpeg diagnostics may contain signed URLs/paths. Never return stderr.
        raise MediaError("MEDIA_TOOL_FAILED") from None


def probe(path: Path) -> dict:
    try:
        data = json.loads(
            _run(
                [
                    os.environ.get("RUNNINGHUB_FFPROBE", "ffprobe"),
                    "-v",
                    "error",
                    "-count_frames",
                    "-show_streams",
                    "-show_format",
                    "-of",
                    "json",
                    str(path),
                ]
            )
        )
        videos = [
            stream for stream in data["streams"] if stream.get("codec_type") == "video"
        ]
        if len(videos) != 1:
            raise ValueError
        stream = videos[0]
        return {
            "width": int(stream["width"]),
            "height": int(stream["height"]),
            "fps": str(Fraction(stream["avg_frame_rate"])),
            "frames": int(stream.get("nb_read_frames") or stream["nb_frames"]),
            "seconds": float(stream.get("duration") or data["format"]["duration"]),
            "audio": any(s.get("codec_type") == "audio" for s in data["streams"]),
        }
    except (ValueError, KeyError, TypeError, ZeroDivisionError):
        raise MediaError("INVALID_VIDEO") from None


def is_main(spec: dict) -> bool:
    return (
        spec["width"] == 576
        and spec["height"] == 1024
        and spec["seconds"] >= 4.95
        and spec["frames"] >= 120
    )


def validate_image(path: Path) -> None:
    _run(
        [
            os.environ.get("RUNNINGHUB_FFMPEG", "ffmpeg"),
            "-v",
            "error",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-f",
            "null",
            "-",
        ]
    )


def finalize(raw: Path, final: Path) -> dict:
    if not is_main(probe(raw)):
        raise MediaError("MAIN_VIDEO_SPEC_MISMATCH")
    part = final.with_name(final.stem + ".part.mp4")
    part.unlink(missing_ok=True)
    try:
        _run(
            [
                os.environ.get("RUNNINGHUB_FFMPEG", "ffmpeg"),
                "-n",
                "-v",
                "error",
                "-i",
                str(raw),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-vf",
                "fps=24",
                "-frames:v",
                "120",
                "-t",
                "5",
                "-c:v",
                "libx264",
                "-threads",
                "2",
                "-crf",
                "18",
                "-preset",
                "fast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(part),
            ]
        )
        spec = probe(part)
        if (
            not is_main(spec)
            or Fraction(spec["fps"]) != 24
            or spec["frames"] != 120
            or abs(spec["seconds"] - 5) > 0.05
        ):
            raise MediaError("FINAL_VIDEO_SPEC_MISMATCH")
        part.replace(final)
        return spec
    finally:
        part.unlink(missing_ok=True)


def dry_run_video(destination: Path) -> None:
    _run(
        [
            os.environ.get("RUNNINGHUB_FFMPEG", "ffmpeg"),
            "-n",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:s=576x1024:r=24",
            "-frames:v",
            "124",
            "-c:v",
            "libx264",
            "-threads",
            "2",
            "-pix_fmt",
            "yuv420p",
            str(destination),
        ]
    )
