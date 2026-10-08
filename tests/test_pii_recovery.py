"""Recovery authenticates the complete edited document before publishing it."""
from __future__ import annotations

import base64
from pathlib import Path

import pytest

from ganglion.adapters.recovery import decode_key, generate_keypair, restore_document
from ganglion.domains.pii.pipeline import run_document
from ganglion.domains.pii.rules import RulesDetector


@pytest.fixture
def bundle(tmp_path):
    source = tmp_path / "source.txt"
    source.write_bytes("공개 접두어😀\r\n이름: 김민수\r\n메일: minsu@example.com\r\n공개 접미어e\u0301\r\n".encode())
    keys = generate_keypair()
    output = tmp_path / "bundle"
    result = run_document(source, output, RulesDetector(), public_key=keys["public_key"], document_id="recovery-test")
    assert result["edits"] == 2
    return source, output, keys


@pytest.mark.parametrize("mutation", ["document_prefix", "document_token", "document_suffix",
                                       "document_truncated", "document_appended", "recovery_header",
                                       "recovery_ciphertext", "recovery_truncated",
                                       "recovery_appended", "wrong_key"])
def test_tampering_wrong_keys_and_truncation_publish_no_partial_restore(tmp_path, bundle, mutation):
    _source, output, keys = bundle
    document = output / "document.txt"
    recovery = output / "recovery.bin"
    private_key = keys["private_key"]
    if mutation.startswith("document_"):
        data = document.read_bytes()
        if mutation == "document_prefix":
            data = bytes([data[0] ^ 1]) + data[1:]
        elif mutation == "document_token":
            data = data.replace(b"<PERSON_1>", b"<PERSON_9>")
        elif mutation == "document_suffix":
            data = data[:-1] + bytes([data[-1] ^ 1])
        elif mutation == "document_truncated":
            data = data[:data.index(b"<PERSON_1>") + 3]
        else:
            data += b"extra bytes"
        document.write_bytes(data)
    elif mutation.startswith("recovery_"):
        data = recovery.read_bytes()
        if mutation == "recovery_header":
            data = data.replace(b"recovery-test", b"recovery-best")
        elif mutation == "recovery_ciphertext":
            data = data[:-1] + bytes([data[-1] ^ 1])
        elif mutation == "recovery_truncated":
            data = data[:-7]
        else:
            data += b"unauthenticated garbage"
        recovery.write_bytes(data)
    else:
        private_key = generate_keypair()["private_key"]
    destination = tmp_path / "restored" / "source.txt"
    with pytest.raises(ValueError):
        restore_document(document, recovery, private_key, destination)
    assert not destination.exists()
    assert not list(destination.parent.glob(".restore-*"))


def test_existing_destination_is_never_overwritten(bundle, tmp_path):
    _source, output, keys = bundle
    destination = tmp_path / "existing.txt"
    destination.write_bytes(b"user-owned contents")
    with pytest.raises(ValueError, match="already exists"):
        restore_document(output / "document.txt", output / "recovery.bin", keys["private_key"], destination)
    assert destination.read_bytes() == b"user-owned contents"


def test_recovery_sidecar_does_not_contain_plaintext_pii(bundle):
    _source, output, keys = bundle
    data = (output / "recovery.bin").read_bytes()
    for value in ("김민수", "minsu@example.com", keys["private_key"]):
        assert value.encode() not in data
        assert base64.b64encode(value.encode()) not in data


@pytest.mark.parametrize("key", ["", "not base64", base64.b64encode(b"x" * 31).decode(),
                                 base64.b64encode(b"x" * 33).decode(), None])
def test_key_decoder_rejects_bad_encoding_and_size(key):
    with pytest.raises(ValueError):
        decode_key(key)


def test_keypair_uses_distinct_valid_32_byte_keys():
    one, two = generate_keypair(), generate_keypair()
    assert one["format"] == "ganglion-x25519-v1"
    assert one != two
    assert len(decode_key(one["public_key"])) == len(decode_key(one["private_key"])) == 32
    assert one["public_key"] != one["private_key"]
