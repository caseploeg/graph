from __future__ import annotations

import shutil
from pathlib import Path


def is_gcs_path(path: str) -> bool:
    return path.startswith("gs://")


def parse_gcs_path(gcs_path: str) -> tuple[str, str]:
    path = gcs_path[5:]
    parts = path.split("/", 1)
    bucket = parts[0]
    prefix = parts[1] if len(parts) > 1 else ""
    return bucket, prefix


def upload_to_gcs(local_path: Path, bucket_name: str, blob_name: str) -> str:
    try:
        from google.cloud import storage
    except ImportError as e:
        raise ImportError(
            "google-cloud-storage is required for GCS uploads. "
            "Install with: uv pip install google-cloud-storage"
        ) from e

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(str(local_path))
    return f"gs://{bucket_name}/{blob_name}"


def copy_to_local(source: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / source.name
    shutil.copy2(source, dest_path)
    return dest_path


def upload_file(local_path: Path, destination: str) -> str:
    if is_gcs_path(destination):
        bucket, prefix = parse_gcs_path(destination)
        blob_name = f"{prefix}/{local_path.name}" if prefix else local_path.name
        return upload_to_gcs(local_path, bucket, blob_name)
    else:
        dest_path = copy_to_local(local_path, Path(destination))
        return str(dest_path)


def upload_directory(source_dir: Path, destination: str, pattern: str = "*.json") -> list[str]:
    uploaded = []
    for file_path in source_dir.glob(pattern):
        if file_path.is_file():
            result = upload_file(file_path, destination)
            uploaded.append(result)
    return uploaded
