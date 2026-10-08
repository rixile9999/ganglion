"""Authenticated streaming recovery. Keys and source values never enter traces."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import struct
import tempfile

MAGIC = b"GANGLION1\n"
MAX_RECORD = 16 * 1024 * 1024


def decode_key(value: str) -> bytes:
    try:
        key = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("key must be base64") from exc
    if len(key) != 32:
        raise ValueError("key must contain 32 bytes")
    return key


def generate_keypair() -> dict:
    from nacl.public import PrivateKey
    key = PrivateKey.generate()
    return {"format": "ganglion-x25519-v1",
            "public_key": base64.b64encode(bytes(key.public_key)).decode(),
            "private_key": base64.b64encode(bytes(key)).decode()}


class RecoveryWriter:
    def __init__(self, path: Path, public_key: str, document_id: str):
        from nacl import bindings as sodium
        from nacl.public import PublicKey, SealedBox
        self.sodium = sodium
        self.key = os.urandom(32)
        self.state = sodium.crypto_secretstream_xchacha20poly1305_state()
        header = sodium.crypto_secretstream_xchacha20poly1305_init_push(self.state, self.key)
        self.header = json.dumps({"format": "sodium-secretstream-v1", "document_id": document_id,
                                  "sealed_key": base64.b64encode(SealedBox(PublicKey(decode_key(public_key))).encrypt(self.key)).decode(),
                                  "stream_header": base64.b64encode(header).decode()}, sort_keys=True).encode()
        self.file = path.open("xb")
        os.chmod(path, 0o600)
        self.file.write(MAGIC + struct.pack(">I", len(self.header)) + self.header)
        self.lookup_path = path.parent / ".lookup.sqlite"
        self.lookup = sqlite3.connect(self.lookup_path)
        os.chmod(self.lookup_path, 0o600)
        self.lookup.execute("CREATE TABLE mapping (kind TEXT, digest TEXT, token TEXT, PRIMARY KEY(kind,digest))")
        self.counts: dict[str, int] = {}

    def pseudonym(self, kind: str, original: bytes) -> str:
        digest = hmac.new(self.key, kind.encode() + b"\0" + original, hashlib.sha256).hexdigest()
        row = self.lookup.execute("SELECT token FROM mapping WHERE kind=? AND digest=?", (kind, digest)).fetchone()
        if row:
            return row[0]
        self.counts[kind] = self.counts.get(kind, 0) + 1
        token = f"<{kind}_{self.counts[kind]}>"
        self.lookup.execute("INSERT INTO mapping VALUES (?,?,?)", (kind, digest, token))
        return token

    def record(self, payload: dict, *, final: bool = False) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        tag = self.sodium.crypto_secretstream_xchacha20poly1305_TAG_FINAL if final else 0
        cipher = self.sodium.crypto_secretstream_xchacha20poly1305_push(self.state, data, ad=self.header, tag=tag)
        if len(cipher) > MAX_RECORD:
            raise ValueError("recovery record too large")
        self.file.write(struct.pack(">I", len(cipher)) + cipher)

    def close(self) -> None:
        self.file.close()
        self.lookup.close()
        self.lookup_path.unlink(missing_ok=True)
        self.key = b""


def restore_document(document: Path, recovery: Path, private_key: str, destination: Path) -> dict:
    """Restore to a private temporary file; publish only after full authentication."""
    from nacl import bindings as sodium
    from nacl.exceptions import CryptoError
    from nacl.public import PrivateKey, SealedBox
    if destination.exists():
        raise ValueError("restore destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with recovery.open("rb") as encrypted, document.open("rb") as source:
            if encrypted.read(len(MAGIC)) != MAGIC:
                raise ValueError("unsupported recovery format")
            size = _length(encrypted, 16384)
            header_raw = _exact(encrypted, size)
            header = json.loads(header_raw)
            key = SealedBox(PrivateKey(decode_key(private_key))).decrypt(base64.b64decode(header["sealed_key"], validate=True))
            state = sodium.crypto_secretstream_xchacha20poly1305_state()
            sodium.crypto_secretstream_xchacha20poly1305_init_pull(state, base64.b64decode(header["stream_header"], validate=True), key)
            original_digest, output_digest = hashlib.sha256(), hashlib.sha256()
            cursor, edits, final = 0, 0, False
            with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".restore-", delete=False) as target:
                temp_name = target.name
                os.chmod(temp_name, 0o600)
                while not final:
                    cipher = _exact(encrypted, _length(encrypted, MAX_RECORD))
                    plaintext, tag = sodium.crypto_secretstream_xchacha20poly1305_pull(state, cipher, ad=header_raw)
                    record = json.loads(plaintext)
                    final = tag == sodium.crypto_secretstream_xchacha20poly1305_TAG_FINAL
                    if final:
                        if record.get("kind") != "final" or encrypted.read(1):
                            raise ValueError("invalid recovery final record")
                        while block := source.read(65536):
                            output_digest.update(block)
                            original_digest.update(block)
                            target.write(block)
                        if (record["edits"] != edits or record["output_sha256"] != output_digest.hexdigest()
                                or record["source_sha256"] != original_digest.hexdigest()):
                            raise ValueError("document and recovery do not match")
                        continue
                    if record.get("kind") != "edit":
                        raise ValueError("invalid recovery edit")
                    start, end = record["output_span"]
                    if type(start) is not int or type(end) is not int or not cursor <= start < end:
                        raise ValueError("invalid recovery offsets")
                    remaining = start - cursor
                    while remaining:
                        block = source.read(min(remaining, 65536))
                        if not block:
                            raise ValueError("document is truncated")
                        remaining -= len(block)
                        output_digest.update(block)
                        original_digest.update(block)
                        target.write(block)
                    replaced = _exact(source, end - start)
                    if replaced != record["token"].encode():
                        raise ValueError("document replacement was modified")
                    output_digest.update(replaced)
                    original = base64.b64decode(record["original"], validate=True)
                    original_digest.update(original)
                    target.write(original)
                    cursor, edits = end, edits + 1
                target.flush()
                os.fsync(target.fileno())
            os.link(temp_name, destination)  # refuses a destination created concurrently
            return {"status": "complete", "edits": edits, "source_sha256": original_digest.hexdigest()}
    except (CryptoError, KeyError, json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError("recovery authentication failed") from exc
    finally:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)


def _length(source, maximum: int) -> int:
    size = struct.unpack(">I", _exact(source, 4))[0]
    if not 0 < size <= maximum:
        raise ValueError("invalid recovery record length")
    return size


def _exact(source, size: int) -> bytes:
    data = source.read(size)
    if len(data) != size:
        raise ValueError("truncated recovery data")
    return data
