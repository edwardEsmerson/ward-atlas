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
import json
import os
import platform
import struct
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path


REPOSITORY = "falkordb/falkordb"
TAG = "6.0.2"
REGISTRY = "https://registry-1.docker.io/v2"
AUTH = "https://auth.docker.io/token"
INDEX_DIGEST = "sha256:107455e862a824e45e8e9f5a7fbfdf5e00a08b14e13eae2577c72fd9e3f59ba6"
AMD64_MANIFEST_DIGEST = "sha256:288aaa7d0583c7c15fb28509544c0418186ef064f551bfb477f3df26b1a892fb"
MODULE_LAYER_DIGEST = "sha256:6c11c298608011dfeb77a91598799a97939bed5dbe17a80e7997356d6e81846b"
MODULE_MEMBER = "var/lib/falkordb/bin/falkordb.so"


def request(url: str, *, token: str | None = None, accept: str = "application/json") -> tuple[dict, bytes]:
    headers = {"Accept": accept, "User-Agent": "WardAtlas-local-workshop/1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=90) as response:
        return {key.lower(): value for key, value in response.headers.items()}, response.read()


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
    sha = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=120) as response, target.open("wb") as out:
        while chunk := response.read(1024 * 1024):
            sha.update(chunk)
            out.write(chunk)
    actual = "sha256:" + sha.hexdigest()
    if actual != expected_digest:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"OCI layer digest mismatch: expected {expected_digest}, got {actual}")


def install(destination: Path) -> dict:
    if platform.machine().lower() not in {"x86_64", "amd64"}:
        raise RuntimeError(f"pinned module is linux/amd64; host architecture is {platform.machine()}")
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

    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
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
            temporary = destination.with_name(destination.name + ".tmp")
            try:
                with source, temporary.open("wb") as out:
                    while chunk := source.read(1024 * 1024):
                        out.write(chunk)
                raw = temporary.read_bytes()
                if len(raw) < 64 or raw[:4] != b"\x7fELF" or raw[4] != 2 or raw[5] != 1:
                    raise RuntimeError("extracted module is not a 64-bit little-endian ELF object")
                if struct.unpack_from("<H", raw, 18)[0] != 62:
                    raise RuntimeError("extracted module ELF machine is not x86_64")
                module_digest = digest(raw)
                os.chmod(temporary, 0o755)
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)

    receipt = {
        "source": f"https://hub.docker.com/r/{REPOSITORY}",
        "artifact": f"{REPOSITORY}:{TAG}",
        "platform": "linux/amd64",
        "image_index_digest": INDEX_DIGEST,
        "platform_manifest_digest": AMD64_MANIFEST_DIGEST,
        "module_layer_digest": MODULE_LAYER_DIGEST,
        "member": MODULE_MEMBER,
        "module_sha256": module_digest.removeprefix("sha256:"),
        "destination": str(destination),
    }
    receipt_path = destination.with_name("PROVENANCE.json")
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--destination", type=Path, default=Path(".local/falkordb/falkordb.so")
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
