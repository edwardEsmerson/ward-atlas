#!/usr/bin/env python3
"""Install only the pinned official FalkorDB module under this repository.

This reads the public FalkorDB OCI artifact directly. It does not invoke Docker,
connect to a Docker socket, extract image filesystems, or use registry credentials.
The anonymous registry token is held in memory only; all downloaded content is
digest-checked before the single expected shared-library member is extracted.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import platform
import struct
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


REPOSITORY = "falkordb/falkordb"
TAG = "6.0.2"
ARTIFACT = f"{REPOSITORY}:{TAG}"
SOURCE = f"https://hub.docker.com/r/{REPOSITORY}"
PLATFORM = "linux/amd64"
REGISTRY = "https://registry-1.docker.io/v2"
AUTH = "https://auth.docker.io/token"
INDEX_DIGEST = "sha256:107455e862a824e45e8e9f5a7fbfdf5e00a08b14e13eae2577c72fd9e3f59ba6"
AMD64_MANIFEST_DIGEST = "sha256:288aaa7d0583c7c15fb28509544c0418186ef064f551bfb477f3df26b1a892fb"
MODULE_LAYER_DIGEST = "sha256:6c11c298608011dfeb77a91598799a97939bed5dbe17a80e7997356d6e81846b"
MODULE_MEMBER = "var/lib/falkordb/bin/falkordb.so"
MODULE_SHA256 = "93bc3c1d484f7fcad45c9409c32034c79e876596b18ce5b03922be007dba889c"
ROOT = Path(__file__).resolve().parents[1]
RETRY_DELAYS_SECONDS = (1, 2)


def with_network_retries(operation, label: str):
    """Retry bounded transient registry failures, never integrity or 4xx errors."""
    attempts = len(RETRY_DELAYS_SECONDS) + 1
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except urllib.error.HTTPError as exc:
            transient = exc.code == 429 or 500 <= exc.code <= 599
            if not transient:
                raise
            error = exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as exc:
            error = exc
        if attempt == attempts:
            raise RuntimeError(
                f"public OCI registry {label} failed after {attempts} attempts: {error}"
            ) from error
        delay = RETRY_DELAYS_SECONDS[attempt - 1]
        print(
            f"Transient public OCI registry failure during {label} "
            f"(attempt {attempt}/{attempts}); retrying in {delay}s: {error}",
            file=sys.stderr,
        )
        time.sleep(delay)


def request(url: str, *, token: str | None = None, accept: str = "application/json") -> tuple[dict, bytes]:
    headers = {"Accept": accept, "User-Agent": "WardAtlas-local-workshop/1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)

    def fetch() -> tuple[dict, bytes]:
        with urllib.request.urlopen(req, timeout=90) as response:
            return {key.lower(): value for key, value in response.headers.items()}, response.read()

    return with_network_retries(fetch, "metadata request")


def get_token() -> str:
    query = urllib.parse.urlencode(
        {"service": "registry.docker.io", "scope": f"repository:{REPOSITORY}:pull"}
    )
    _, body = request(f"{AUTH}?{query}")
    token = json.loads(body).get("token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("public OCI registry did not provide an anonymous pull token")
    return token


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def write_blob(url: str, token: str, target: Path, expected_digest: str) -> None:
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "User-Agent": "WardAtlas-local-workshop/1"},
    )

    def download() -> None:
        sha = hashlib.sha256()
        with urllib.request.urlopen(req, timeout=120) as response, target.open("wb") as out:
            while chunk := response.read(1024 * 1024):
                sha.update(chunk)
                out.write(chunk)
        actual = "sha256:" + sha.hexdigest()
        if actual != expected_digest:
            target.unlink(missing_ok=True)
            raise RuntimeError(f"OCI layer digest mismatch: expected {expected_digest}, got {actual}")

    with_network_retries(download, "pinned module-layer download")


def install(destination: Path) -> dict:
    if platform.machine().lower() not in {"x86_64", "amd64"}:
        raise RuntimeError(f"pinned module is linux/amd64; host architecture is {platform.machine()}")
    destination = Path(os.path.abspath(destination))
    canonical_parent = destination.parent.resolve()
    if not canonical_parent.is_relative_to(ROOT):
        raise RuntimeError("FalkorDB module destination must remain inside this project")
    canonical_parent.mkdir(parents=True, exist_ok=True)
    canonical_parent = canonical_parent.resolve()
    if not canonical_parent.is_relative_to(ROOT):
        raise RuntimeError("FalkorDB module destination must remain inside this project")
    # Keep the final path lexical: os.replace below replaces a pre-existing
    # symlink rather than resolving it and writing through it.
    destination = canonical_parent / destination.name
    token = get_token()
    index_headers, index_bytes = request(
        f"{REGISTRY}/{REPOSITORY}/manifests/{TAG}",
        token=token,
        accept="application/vnd.oci.image.index.v1+json",
    )
    if digest(index_bytes) != INDEX_DIGEST or index_headers.get("docker-content-digest") != INDEX_DIGEST:
        raise RuntimeError("official image index digest changed; refusing an unpinned artifact")
    index = json.loads(index_bytes)
    amd64 = [
        item
        for item in index.get("manifests", [])
        if item.get("platform", {}).get("os") == "linux"
        and item.get("platform", {}).get("architecture") == "amd64"
    ]
    if len(amd64) != 1 or amd64[0].get("digest") != AMD64_MANIFEST_DIGEST:
        raise RuntimeError("pinned linux/amd64 manifest is missing or changed")

    manifest_headers, manifest_bytes = request(
        f"{REGISTRY}/{REPOSITORY}/manifests/{AMD64_MANIFEST_DIGEST}",
        token=token,
        accept="application/vnd.oci.image.manifest.v1+json",
    )
    if digest(manifest_bytes) != AMD64_MANIFEST_DIGEST or manifest_headers.get("docker-content-digest") != AMD64_MANIFEST_DIGEST:
        raise RuntimeError("amd64 image manifest digest mismatch")
    manifest = json.loads(manifest_bytes)
    module_layers = [
        layer for layer in manifest.get("layers", []) if layer.get("digest") == MODULE_LAYER_DIGEST
    ]
    if len(module_layers) != 1:
        raise RuntimeError("pinned module layer is missing or ambiguous")

    with tempfile.TemporaryDirectory(prefix="wardatlas-falkordb-") as temp_dir:
        layer_path = Path(temp_dir) / "module-layer.tar.gz"
        write_blob(
            f"{REGISTRY}/{REPOSITORY}/blobs/{MODULE_LAYER_DIGEST}",
            token,
            layer_path,
            MODULE_LAYER_DIGEST,
        )
        with tarfile.open(layer_path, mode="r:gz") as archive:
            members = [m for m in archive.getmembers() if m.name == MODULE_MEMBER]
            if len(members) != 1 or not members[0].isreg() or members[0].size <= 0:
                raise RuntimeError("expected exactly one regular FalkorDB shared library in pinned layer")
            source = archive.extractfile(members[0])
            if source is None:
                raise RuntimeError("cannot read FalkorDB module from pinned layer")
            temp_fd, temp_name = tempfile.mkstemp(
                prefix=destination.name + ".", suffix=".tmp", dir=destination.parent
            )
            temporary = Path(temp_name)
            try:
                with source, os.fdopen(temp_fd, "wb") as out:
                    while chunk := source.read(1024 * 1024):
                        out.write(chunk)
                raw = temporary.read_bytes()
                if len(raw) < 64 or raw[:4] != b"\x7fELF" or raw[4] != 2 or raw[5] != 1:
                    raise RuntimeError("extracted module is not a 64-bit little-endian ELF object")
                if struct.unpack_from("<H", raw, 18)[0] != 62:
                    raise RuntimeError("extracted module ELF machine is not x86_64")
                module_digest = digest(raw)
                if module_digest.removeprefix("sha256:") != MODULE_SHA256:
                    raise RuntimeError(
                        "extracted FalkorDB module SHA-256 differs from the pinned 6.0.2 artifact"
                    )
                os.chmod(temporary, 0o755)
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)

    receipt = {
        "source": SOURCE,
        "artifact": ARTIFACT,
        "platform": PLATFORM,
        "image_index_digest": INDEX_DIGEST,
        "platform_manifest_digest": AMD64_MANIFEST_DIGEST,
        "module_layer_digest": MODULE_LAYER_DIGEST,
        "member": MODULE_MEMBER,
        "module_sha256": module_digest.removeprefix("sha256:"),
        "destination": str(destination),
    }
    receipt_path = destination.with_name("PROVENANCE.json")
    receipt_fd, receipt_name = tempfile.mkstemp(
        prefix=receipt_path.name + ".", suffix=".tmp", dir=destination.parent
    )
    receipt_temporary = Path(receipt_name)
    try:
        with os.fdopen(receipt_fd, "w", encoding="utf-8") as receipt_file:
            receipt_file.write(json.dumps(receipt, indent=2) + "\n")
        os.replace(receipt_temporary, receipt_path)
    finally:
        receipt_temporary.unlink(missing_ok=True)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--destination", type=Path, default=ROOT / ".local/falkordb/falkordb.so"
    )
    args = parser.parse_args()
    try:
        receipt = install(args.destination)
    except Exception as exc:  # preserve a useful command-line failure for local setup
        print(f"FalkorDB module install failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
