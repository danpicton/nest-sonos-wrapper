"""
Shanocast device authentication for Google Cast.

Uses precomputed RSA signatures (795 entries, 2-day buckets covering
2023-08-15 → 2027-12-20) and AirReceiver's certificate chain so the
receiver passes auth checks on stock Android/Chrome clients without
needing a genuine Google-issued device certificate.

Technique: https://xakcop.com/post/shanocast/
Signatures extracted from: https://github.com/rgerganov/shanocast
"""
from __future__ import annotations

import base64
import datetime
import importlib.resources
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_der_private_key

from sonos_cast import cast_channel_pb2 as pb

# ---------------------------------------------------------------------------
# Timing constants (match values embedded in the shanocast patch)
# ---------------------------------------------------------------------------
START_UNIX = 1692057600  # 2023-08-15 00:00:00 UTC
STEP = 172800            # 2 days in seconds
TOTAL_SIGNATURES = 795

# Fixed CN and serial used for the deterministic TLS server certificate.
# Serial: openscreen's CreateCertificateInternal uses a `static int` counter
# initialised by shanocast to 0x51c9ac6. By the time the TLS cert is built it
# is the 4th certificate created in the chain (root, intermediate, device,
# then this TLS cert), so the counter has post-incremented three times.
# Getting this byte-perfect is required: shanocast's precomputed Cast V2
# auth signatures cover the resulting cert DER exactly.
_TLS_CERT_CN = "4aa9ca2e-c340-11ea-8000-18ba395587df"
_TLS_CERT_SERIAL = 0x51C9AC9

# ---------------------------------------------------------------------------
# Static blobs
# ---------------------------------------------------------------------------

def _load_blob(filename: str) -> bytes:
    ref = importlib.resources.files("sonos_cast").joinpath(filename)
    return ref.read_bytes()


# AirReceiver leaf cert (943 bytes) — sent as client_auth_certificate
AUTH_CRT_DER: bytes = _load_blob("auth_crt.der")

# Eureka Gen1 ICA (907 bytes) — sent as intermediate_certificate
INTERMEDIATE_CRT_DER: bytes = _load_blob("intermediate_crt.der")

# AirReceiver RSA-2048 private key in PKCS#8 DER (1190 bytes)
_PEER_KEY_DER: bytes = _load_blob("peer_key.der")

# Flat binary blob: 795 × 256-byte precomputed signatures
_SIGNATURES_BLOB: bytes = _load_blob("signatures.bin")

# ---------------------------------------------------------------------------
# Minimal DER encoder (only the constructs needed for a simple x509 cert)
# ---------------------------------------------------------------------------

def _der_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    elif n < 0x100:
        return bytes([0x81, n])
    else:
        return bytes([0x82, n >> 8, n & 0xFF])

def _tlv(tag: int, content: bytes) -> bytes:
    return bytes([tag]) + _der_len(len(content)) + content

def _seq(*parts: bytes) -> bytes:
    return _tlv(0x30, b"".join(parts))

def _set(*parts: bytes) -> bytes:
    return _tlv(0x31, b"".join(parts))

def _integer(n: int) -> bytes:
    if n == 0:
        return _tlv(0x02, b"\x00")
    octets: list[int] = []
    while n:
        octets.append(n & 0xFF)
        n >>= 8
    octets.reverse()
    if octets[0] & 0x80:
        octets.insert(0, 0x00)
    return _tlv(0x02, bytes(octets))

def _oid(raw: bytes) -> bytes:
    return _tlv(0x06, raw)

def _utf8str(s: str) -> bytes:
    return _tlv(0x0C, s.encode())

def _utctime(dt: datetime.datetime) -> bytes:
    # UTCTime format: YYMMDDHHMMSSZ
    return _tlv(0x17, dt.strftime("%y%m%d%H%M%SZ").encode())

def _bitstring(data: bytes) -> bytes:
    # 0x00 = no unused bits
    return _tlv(0x03, b"\x00" + data)

def _explicit(tag: int, content: bytes) -> bytes:
    return _tlv(0xA0 | tag, content)

# OID byte strings
_OID_SHA1_WITH_RSA = bytes([0x2A, 0x86, 0x48, 0x86, 0xF7, 0x0D, 0x01, 0x01, 0x05])
_OID_RSA           = bytes([0x2A, 0x86, 0x48, 0x86, 0xF7, 0x0D, 0x01, 0x01, 0x01])
_OID_CN            = bytes([0x55, 0x04, 0x03])

def _sha1_rsa_algid() -> bytes:
    return _seq(_oid(_OID_SHA1_WITH_RSA), b"\x05\x00")  # NULL params

def _name(cn: str) -> bytes:
    return _seq(_set(_seq(_oid(_OID_CN), _utf8str(cn))))

def _build_cert_der(not_before_ts: int) -> bytes:
    """Build a DER-encoded self-signed cert with SHA-1 signature."""
    key = load_der_private_key(_PEER_KEY_DER, password=None)
    spki = key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    not_before = datetime.datetime.fromtimestamp(not_before_ts, tz=datetime.timezone.utc)
    not_after  = datetime.datetime.fromtimestamp(not_before_ts + STEP, tz=datetime.timezone.utc)

    name = _name(_TLS_CERT_CN)
    tbs = _seq(
        _explicit(0, _integer(2)),        # version: v3
        _integer(_TLS_CERT_SERIAL),
        _sha1_rsa_algid(),
        name,                              # issuer
        _seq(_utctime(not_before), _utctime(not_after)),
        name,                              # subject (self-signed)
        spki,
        # No extensions — matches shanocast's patched openscreen, which removes
        # the keyUsage extension and adds none for non-CA TLS certs.
    )
    sig = key.sign(tbs, padding.PKCS1v15(), hashes.SHA1())  # noqa: S303
    return _seq(tbs, _sha1_rsa_algid(), _bitstring(sig))

# ---------------------------------------------------------------------------
# Signature lookup
# ---------------------------------------------------------------------------

def _bucket_index(ts: int) -> int:
    index = (ts - START_UNIX) // STEP
    if index < 0 or index >= TOTAL_SIGNATURES:
        last = datetime.datetime.utcfromtimestamp(
            START_UNIX + (TOTAL_SIGNATURES - 1) * STEP
        ).date()
        raise ValueError(
            f"Timestamp {ts} is out of range for precomputed signatures "
            f"(index {index}, valid 0–{TOTAL_SIGNATURES - 1}). "
            f"Coverage ends {last}."
        )
    return index


def signature_for_time(ts: int) -> bytes:
    """Return the 256-byte precomputed signature for the 2-day bucket containing *ts*."""
    idx = _bucket_index(ts)
    return _SIGNATURES_BLOB[idx * 256 : (idx + 1) * 256]


# ---------------------------------------------------------------------------
# TLS certificate (deterministic per 2-day bucket, fixed private key)
# ---------------------------------------------------------------------------

def build_tls_cert_pem(ts: int) -> tuple[bytes, bytes]:
    """
    Build the TLS server cert/key PEM pair for the bucket containing *ts*.

    Uses AirReceiver's fixed private key. The notBefore is pinned to the
    bucket start so it matches the bytes the precomputed signatures cover.
    """
    idx = _bucket_index(ts)
    bucket_start = START_UNIX + idx * STEP

    cert_der = _build_cert_der(bucket_start)
    cert_pem = (
        b"-----BEGIN CERTIFICATE-----\n"
        + base64.encodebytes(cert_der)
        + b"-----END CERTIFICATE-----\n"
    )

    key = load_der_private_key(_PEER_KEY_DER, password=None)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    return cert_pem, key_pem


# ---------------------------------------------------------------------------
# AuthResponse builder
# ---------------------------------------------------------------------------

def build_auth_response(challenge: pb.DeviceAuthMessage) -> pb.DeviceAuthMessage:
    """
    Construct a DeviceAuthMessage response for the given challenge.

    The precomputed signatures are RSASSA_PKCS1v15 over SHA-256 of the TLS
    cert DER alone (no nonce). We therefore:
    - report hash_algorithm=SHA256 in the response (modern GMS challenges
      always request SHA256 anyway, but reporting it explicitly avoids any
      verifier ambiguity);
    - intentionally omit `sender_nonce` from the response so chromium's
      verifier reconstructs signed_data as just `peer_cert_der` (it
      concatenates response.sender_nonce with peer_cert_der; an empty nonce
      means the precomputed signature applies). See chromium's
      cast_auth_util.cc::AuthenticateChallengeReply.
    """
    sig = signature_for_time(int(time.time()))

    resp = pb.DeviceAuthMessage()
    resp.response.signature = sig
    resp.response.client_auth_certificate = AUTH_CRT_DER
    resp.response.intermediate_certificate.append(INTERMEDIATE_CRT_DER)
    resp.response.signature_algorithm = pb.RSASSA_PKCS1v15
    resp.response.hash_algorithm = pb.SHA256
    return resp
