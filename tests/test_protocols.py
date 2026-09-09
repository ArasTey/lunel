"""Lunel Core protocol unit tests: VLESS/Trojan header codecs, SS AEAD
streams, quota policy, share links."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))

import lunel_core.relay.vless as vless_mod
from lunel_core.relay.trojan import (
    build_trojan_request,
    parse_trojan_request,
    trojan_password_hash,
)
from lunel_core.relay.vless import build_vless_header, parse_vless_header
from lunel_core.state import Link


def test_vless_header_roundtrip_domain():
    uuid = "123e4567-e89b-12d3-a456-426614174000"
    header = build_vless_header(uuid, "example.com", 443)
    command, address, port, rest = parse_vless_header(header + b"payload")
    assert command == 1
    assert address == "example.com"
    assert port == 443
    assert rest == b"payload"


def test_vless_header_roundtrip_ipv4():
    uuid = "123e4567-e89b-12d3-a456-426614174000"
    header = build_vless_header(uuid, "1.2.3.4", 8080)
    command, address, port, rest = parse_vless_header(header)
    assert address == "1.2.3.4"
    assert port == 8080
    assert rest == b""


def test_trojan_header_roundtrip():
    password = "5f8c1a2b-9de0-4f77-8c21-aabbccddeeff"
    request = build_trojan_request(password, "example.org", 993, b"hello")
    address, port, payload = parse_trojan_request(request, trojan_password_hash(password))
    assert address == "example.org"
    assert port == 993
    assert payload == b"hello"


def test_trojan_wrong_password_rejected():
    from contextlib import suppress

    good = trojan_password_hash("right")
    request = build_trojan_request("wrong", "example.org", 993)
    raised = False
    with suppress(PermissionError):
        try:
            parse_trojan_request(request, good)
        except PermissionError:
            raised = True
    assert raised


def test_ss_aead_roundtrip():
    from lunel_core.relay.shadowsocks import AEADStream, derive_key

    cipher = "chacha20-ietf-poly1305"
    master = derive_key("hunter2-secret", 32)
    enc = AEADStream(master, cipher)
    enc.set_master_key(master)
    dec = AEADStream(master, cipher)
    dec.set_master_key(master)

    wire = b""
    for chunk in (b"first frame", b"second", b"third " * 50):
        wire += enc.encrypt_chunk(chunk)
    dec.feed(wire)
    out = list(dec.try_decrypt_chunks())
    assert out[0] == b"first frame"
    assert b"".join(out[1:]) == b"second" + b"third " * 50


def test_link_quota_policy():
    link = Link(uuid="a" * 32, label="t", protocol="vless-ws", limit_bytes=100, used_bytes=95)
    assert link.is_allowed() is True
    link.used_bytes = 100
    assert link.is_allowed() is False
    link.limit_bytes = 0  # unlimited
    assert link.is_allowed() is True
