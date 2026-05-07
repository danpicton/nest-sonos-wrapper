"""Tests for self-signed TLS certificate generation."""
import ssl
import tempfile
import os

import pytest
from cryptography import x509

from sonos_cast.cert import generate_self_signed_cert, write_cert_files


def test_generate_returns_cert_and_key_bytes():
    cert_pem, key_pem = generate_self_signed_cert("Test Device")
    assert cert_pem.startswith(b"-----BEGIN CERTIFICATE-----")
    assert key_pem.startswith(b"-----BEGIN RSA PRIVATE KEY-----") or key_pem.startswith(
        b"-----BEGIN PRIVATE KEY-----"
    )


def test_generated_cert_is_parseable():
    cert_pem, _ = generate_self_signed_cert("Sonos Test")
    cert = x509.load_pem_x509_certificate(cert_pem)
    cn = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value
    assert cn == "Sonos Test"


def test_write_cert_files_creates_readable_ssl_context():
    cert_pem, key_pem = generate_self_signed_cert("Test")
    with tempfile.TemporaryDirectory() as tmpdir:
        cert_path = os.path.join(tmpdir, "cert.pem")
        key_path = os.path.join(tmpdir, "key.pem")
        write_cert_files(cert_pem, key_pem, cert_path, key_path)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)  # must not raise
