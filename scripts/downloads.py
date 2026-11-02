"""Resumable HTTPS downloads shared by bootstrap and model preparation."""
from __future__ import annotations

import hashlib
import re
import shutil
import urllib.request
import urllib.error
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, target: Path, expected: str | None = None) -> Path:
    if not url.startswith("https://"):
        raise ValueError("Downloads precisam usar HTTPS")
    receipt = target.with_name(target.name + ".sha256")
    if target.is_file() and target.stat().st_size:
        checksum = expected or (receipt.read_text().strip() if receipt.exists() else None)
        if checksum and sha256(target) != checksum:
            raise ValueError(f"Integridade inválida: {target.name}. Mova o arquivo danificado e execute novamente.")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    offset = partial.stat().st_size if partial.exists() else 0
    headers = {"User-Agent": "Luna-bootstrap/1", "Accept-Encoding": "identity"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
        # Only resume a representation whose validator is known.
        validator = partial.with_name(partial.name + ".etag")
        if validator.exists():
            headers["If-Range"] = validator.read_text()
        else:
            offset = 0
            headers.pop("Range")
    try:
        response = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120)
    except urllib.error.HTTPError as exc:
        if exc.code != 416 or not offset:
            raise
        # A complete .part left by a power loss must not get stuck at Range: size-.
        offset = 0
        headers.pop("Range", None)
        headers.pop("If-Range", None)
        response = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120)
    with response:
        if response.status != 206:
            offset = 0
        elif not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
            raise ValueError("Servidor devolveu intervalo de download incorreto")
        length = int(response.headers.get("Content-Length", "0"))
        if length and shutil.disk_usage(target.parent).free < length + 64 * 1024 ** 2:
            raise OSError(f"Sem espaço para baixar {target.name}")
        etag = response.headers.get("ETag")
        if etag:
            partial.with_name(partial.name + ".etag").write_text(etag)
        linked = response.headers.get("X-Linked-Etag", "").strip('"')
        if expected is None and re.fullmatch(r"[0-9a-f]{64}", linked):
            expected = linked
        received = 0
        with partial.open("ab" if offset else "wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                received += len(chunk)
        if length and received != length:
            raise OSError(f"Download incompleto: {target.name}; execute novamente para retomar")
    actual = sha256(partial)
    if expected and actual != expected:
        # Do not append to a complete but corrupt representation on the next run.
        partial.with_name(partial.name + ".etag").unlink(missing_ok=True)
        raise ValueError(f"Checksum inválido: {target.name}; o próximo início refará este arquivo")
    partial.replace(target)
    receipt.write_text(actual + "\n")
    partial.with_name(partial.name + ".etag").unlink(missing_ok=True)
    return target
