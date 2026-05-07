"""Tests for shanocast Cast device authentication."""
import datetime
import ssl
import time

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from sonos_cast.cast_auth import (
    AUTH_CRT_DER,
    INTERMEDIATE_CRT_DER,
    START_UNIX,
    STEP,
    TOTAL_SIGNATURES,
    build_auth_response,
    build_tls_cert_pem,
    signature_for_time,
)
from sonos_cast import cast_channel_pb2 as pb


# ---------------------------------------------------------------------------
# Constants / blobs
# ---------------------------------------------------------------------------


def test_auth_crt_is_943_bytes():
    assert len(AUTH_CRT_DER) == 943


def test_intermediate_crt_is_907_bytes():
    assert len(INTERMEDIATE_CRT_DER) == 907


def test_signatures_cover_795_entries():
    assert TOTAL_SIGNATURES == 795


def test_start_unix_is_2023_08_15():
    dt = datetime.datetime.utcfromtimestamp(START_UNIX)
    assert (dt.year, dt.month, dt.day) == (2023, 8, 15)


def test_step_is_two_days():
    assert STEP == 172800


# ---------------------------------------------------------------------------
# signature_for_time
# ---------------------------------------------------------------------------


def test_signature_for_time_returns_256_bytes():
    sig = signature_for_time(START_UNIX)
    assert len(sig) == 256


def test_signature_for_time_first_bucket():
    sig0 = signature_for_time(START_UNIX)
    sig1 = signature_for_time(START_UNIX + STEP)
    assert sig0 != sig1


def test_signature_for_time_same_bucket_same_result():
    t = START_UNIX + 5 * STEP + 100  # mid-bucket
    assert signature_for_time(t) == signature_for_time(t + 3600)


def test_signature_for_time_last_bucket():
    last_bucket_start = START_UNIX + (TOTAL_SIGNATURES - 1) * STEP
    sig = signature_for_time(last_bucket_start)
    assert len(sig) == 256


def test_signature_for_time_today_is_in_range():
    sig = signature_for_time(int(time.time()))
    assert len(sig) == 256


def test_signature_for_time_raises_past_coverage():
    far_future = START_UNIX + (TOTAL_SIGNATURES + 10) * STEP
    with pytest.raises(ValueError, match="out of range"):
        signature_for_time(far_future)


def test_signature_for_time_raises_before_start():
    with pytest.raises(ValueError, match="out of range"):
        signature_for_time(START_UNIX - 1)


# ---------------------------------------------------------------------------
# build_tls_cert_pem
# ---------------------------------------------------------------------------


def test_build_tls_cert_pem_returns_pem_bytes():
    cert_pem, key_pem = build_tls_cert_pem(START_UNIX)
    assert cert_pem.startswith(b"-----BEGIN CERTIFICATE-----")
    assert key_pem.startswith(b"-----BEGIN")


def test_build_tls_cert_pem_cn_is_fixed_uuid():
    cert_pem, _ = build_tls_cert_pem(START_UNIX)
    cert = x509.load_pem_x509_certificate(cert_pem)
    cn = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value
    assert cn == "4aa9ca2e-c340-11ea-8000-18ba395587df"


def test_build_tls_cert_pem_not_before_matches_bucket():
    bucket_start = START_UNIX + 10 * STEP
    cert_pem, _ = build_tls_cert_pem(bucket_start + 3600)  # mid-bucket
    cert = x509.load_pem_x509_certificate(cert_pem)
    not_before_ts = int(cert.not_valid_before_utc.timestamp())
    assert not_before_ts == bucket_start


def test_build_tls_cert_pem_duration_is_two_days():
    cert_pem, _ = build_tls_cert_pem(START_UNIX)
    cert = x509.load_pem_x509_certificate(cert_pem)
    delta = cert.not_valid_after_utc - cert.not_valid_before_utc
    assert delta.total_seconds() == STEP


def test_build_tls_cert_pem_same_key_across_calls():
    _, key1 = build_tls_cert_pem(START_UNIX)
    _, key2 = build_tls_cert_pem(START_UNIX + STEP)
    # Both should use the same fixed underlying key
    k1 = serialization.load_pem_private_key(key1, password=None)
    k2 = serialization.load_pem_private_key(key2, password=None)
    pub1 = k1.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    pub2 = k2.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    assert pub1 == pub2


def test_build_tls_cert_produces_valid_ssl_context(tmp_path):
    cert_pem, key_pem = build_tls_cert_pem(int(time.time()))
    cert_path = tmp_path / "cert.pem"
    key_path  = tmp_path / "key.pem"
    cert_path.write_bytes(cert_pem)
    key_path.write_bytes(key_pem)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))


# ---------------------------------------------------------------------------
# build_auth_response
# ---------------------------------------------------------------------------


def test_build_auth_response_returns_device_auth_message():
    challenge = pb.DeviceAuthMessage()
    challenge.challenge.CopyFrom(pb.AuthChallenge())
    msg = build_auth_response(challenge)
    assert isinstance(msg, pb.DeviceAuthMessage)


def test_build_auth_response_sets_signature():
    challenge = pb.DeviceAuthMessage()
    challenge.challenge.CopyFrom(pb.AuthChallenge())
    resp = build_auth_response(challenge)
    assert len(resp.response.signature) == 256


def test_build_auth_response_sets_client_auth_certificate():
    challenge = pb.DeviceAuthMessage()
    challenge.challenge.CopyFrom(pb.AuthChallenge())
    resp = build_auth_response(challenge)
    assert resp.response.client_auth_certificate == AUTH_CRT_DER


def test_build_auth_response_sets_intermediate_certificate():
    challenge = pb.DeviceAuthMessage()
    challenge.challenge.CopyFrom(pb.AuthChallenge())
    resp = build_auth_response(challenge)
    assert bytes(resp.response.intermediate_certificate[0]) == INTERMEDIATE_CRT_DER


# ---------------------------------------------------------------------------
# Cryptographic guarantees on the precomputed signatures
# ---------------------------------------------------------------------------


def _verify_precomputed_signature(bucket_index: int) -> None:
    """Verify a shanocast precomputed signature against the TLS cert it covers.

    Reproduces what Cast SDK clients do: signed_data = peer_cert_der (we don't
    echo sender_nonce), verified with the auth cert's public key using
    RSASSA_PKCS1v15 + SHA-256.
    """
    from sonos_cast.cast_auth import _build_cert_der, signature_for_time
    bucket_start = START_UNIX + bucket_index * STEP
    cert_der = _build_cert_der(bucket_start)
    sig = signature_for_time(bucket_start)
    auth_cert = x509.load_der_x509_certificate(AUTH_CRT_DER)
    auth_cert.public_key().verify(
        sig, cert_der, padding.PKCS1v15(), hashes.SHA256()
    )


@pytest.mark.parametrize("bucket", [0, 1, 100, 300, 500, 794])
def test_precomputed_signature_validates_against_tls_cert_der(bucket):
    # If this regresses, our cert encoding has drifted from shanocast's
    # BoringSSL output and the device will fail Cast V2 auth on every modern
    # client. The serial number, name encoding, version marker, validity
    # bucketing, and absence of extensions must all stay byte-identical.
    _verify_precomputed_signature(bucket)


def test_precomputed_signature_validates_for_today():
    import time as _time
    from sonos_cast.cast_auth import _build_cert_der, signature_for_time
    now = int(_time.time())
    bucket_start = START_UNIX + ((now - START_UNIX) // STEP) * STEP
    cert_der = _build_cert_der(bucket_start)
    sig = signature_for_time(now)
    auth_cert = x509.load_der_x509_certificate(AUTH_CRT_DER)
    auth_cert.public_key().verify(
        sig, cert_der, padding.PKCS1v15(), hashes.SHA256()
    )


def test_build_auth_response_signature_is_validatable_by_cast_sdk_logic():
    """End-to-end: the response we build is what a Cast SDK client would accept."""
    from sonos_cast.cast_auth import _build_cert_der
    now = int(time.time())
    bucket_start = START_UNIX + ((now - START_UNIX) // STEP) * STEP
    cert_der = _build_cert_der(bucket_start)
    challenge = pb.DeviceAuthMessage()
    challenge.challenge.CopyFrom(pb.AuthChallenge())
    challenge.challenge.sender_nonce = b"\x01\x02\x03\x04\x05\x06\x07\x08"
    challenge.challenge.hash_algorithm = pb.SHA256
    resp = build_auth_response(challenge)
    auth_cert = x509.load_der_x509_certificate(bytes(resp.response.client_auth_certificate))
    # signed_data = response.sender_nonce + peer_cert_der (chromium logic).
    # We omit sender_nonce, so signed_data is just peer_cert_der.
    nonce_response = bytes(resp.response.sender_nonce)
    signed_data = nonce_response + cert_der
    assert resp.response.hash_algorithm == pb.SHA256
    assert nonce_response == b""  # we deliberately don't echo
    auth_cert.public_key().verify(
        bytes(resp.response.signature), signed_data, padding.PKCS1v15(), hashes.SHA256()
    )
