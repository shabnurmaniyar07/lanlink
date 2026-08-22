"""Device identity certificates, fingerprint pinning and secret protection.

LanLink does not use a certificate authority. Each node generates one
self-signed certificate tied to its ``device_id``; peers pin that exact
certificate when they pair. The pinned certificate *is* the device identity, so
an attacker who takes over the address cannot impersonate the device.
"""

from __future__ import annotations

import base64
import contextlib
import datetime as dt
import hashlib
import os
import ssl
import sys
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

CERT_VALID_DAYS = 3650
CERT_RENEW_BEFORE_DAYS = 30


@dataclass
class DeviceCertificate:
    certificate_path: Path
    key_path: Path
    pem: str
    fingerprint: str

    @property
    def short_fingerprint(self) -> str:
        """First 16 hex characters, grouped — what a person can compare out loud."""
        raw = self.fingerprint.replace(":", "")[:16].upper()
        return " ".join(raw[index : index + 4] for index in range(0, len(raw), 4))


def fingerprint_of_der(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


def fingerprint_of_pem(pem: str | bytes) -> str:
    data = pem.encode("utf-8") if isinstance(pem, str) else pem
    certificate = x509.load_pem_x509_certificate(data)
    return fingerprint_of_der(certificate.public_bytes(serialization.Encoding.DER))


def short_fingerprint(fingerprint: str) -> str:
    raw = (fingerprint or "").replace(":", "")[:16].upper()
    return " ".join(raw[index : index + 4] for index in range(0, len(raw), 4))


def _restrict(path: Path) -> None:
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


def _build_certificate(device_id: str, device_name: str, addresses: list[str]):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, f"lanlink-{device_id[:8]}"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "LanLink"),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, device_id),
        ]
    )
    names: list[x509.GeneralName] = [x509.DNSName(f"lanlink-{device_id[:8]}.local")]
    for address in addresses:
        try:
            import ipaddress

            names.append(x509.IPAddress(ipaddress.ip_address(address)))
        except ValueError:
            continue
    if device_name:
        names.append(x509.DNSName(device_name.replace(" ", "-")[:60] or "lanlink"))

    now = dt.datetime.now(dt.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=CERT_VALID_DAYS))
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return key, certificate


def ensure_device_certificate(
    directory: Path, device_id: str, device_name: str = "", addresses: list[str] | None = None
) -> DeviceCertificate:
    """Load this device's certificate, generating one on first run."""
    directory.mkdir(parents=True, exist_ok=True)
    certificate_path = directory / "device-cert.pem"
    key_path = directory / "device-key.pem"

    if certificate_path.exists() and key_path.exists():
        try:
            pem = certificate_path.read_text(encoding="utf-8")
            certificate = x509.load_pem_x509_certificate(pem.encode("utf-8"))
            expires = certificate.not_valid_after_utc
            if expires - dt.datetime.now(dt.UTC) > dt.timedelta(days=CERT_RENEW_BEFORE_DAYS):
                return DeviceCertificate(
                    certificate_path=certificate_path,
                    key_path=key_path,
                    pem=pem,
                    fingerprint=fingerprint_of_der(certificate.public_bytes(serialization.Encoding.DER)),
                )
        except (ValueError, OSError):
            pass  # regenerate below

    key, certificate = _build_certificate(device_id, device_name, addresses or [])
    pem = certificate.public_bytes(serialization.Encoding.PEM).decode("utf-8")
    certificate_path.write_text(pem, encoding="utf-8")
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    _restrict(key_path)
    _restrict(certificate_path)
    return DeviceCertificate(
        certificate_path=certificate_path,
        key_path=key_path,
        pem=pem,
        fingerprint=fingerprint_of_der(certificate.public_bytes(serialization.Encoding.DER)),
    )


def pinned_ssl_context(peer_pem: str) -> ssl.SSLContext:
    """Trust exactly one certificate — the peer's — and nothing else.

    Hostname checking is off on purpose: LanLink devices are reached by IP on a
    LAN where addresses change. The pinned certificate carries the identity.
    """
    context = ssl.create_default_context(cadata=peer_pem)
    context.check_hostname = False
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def fetch_peer_certificate(host: str, port: int, timeout: float = 6.0) -> str:
    """Read a peer's certificate before trusting it, so we can pin and show it."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    import socket

    with (
        socket.create_connection((host, port), timeout=timeout) as raw,
        context.wrap_socket(raw, server_hostname=host) as tls,
    ):
        der = tls.getpeercert(binary_form=True)
    if not der:
        raise ConnectionError("The other device did not present a certificate.")
    return ssl.DER_cert_to_PEM_cert(der)


# --------------------------------------------------------------- secret storage


def _dpapi(data: bytes, unprotect: bool) -> bytes | None:
    """Windows DPAPI through ctypes: no extra dependency, per-user key."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def to_blob(payload: bytes) -> Blob:
        buffer = ctypes.create_string_buffer(payload, len(payload))
        return Blob(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))

    source = to_blob(data)
    result = Blob()
    function = (
        ctypes.windll.crypt32.CryptUnprotectData if unprotect else ctypes.windll.crypt32.CryptProtectData
    )
    arguments = (
        (ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result))
        if unprotect
        else (ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result))
    )
    if not function(*arguments):
        return None
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)


PROTECTED_PREFIX = "dpapi:"


def protect_secret(value: str) -> str:
    """Encrypt a credential for storage where the OS offers a per-user key."""
    if not value:
        return value
    sealed = _dpapi(value.encode("utf-8"), unprotect=False)
    if sealed is None:
        return value  # POSIX: the 0600 settings file is the protection
    return PROTECTED_PREFIX + base64.b64encode(sealed).decode("ascii")


def unprotect_secret(value: str) -> str:
    if not value or not value.startswith(PROTECTED_PREFIX):
        return value
    try:
        sealed = base64.b64decode(value[len(PROTECTED_PREFIX) :])
    except (ValueError, TypeError):
        return ""
    opened = _dpapi(sealed, unprotect=True)
    if opened is None:
        return ""
    return opened.decode("utf-8", "replace")


def secrets_are_protected() -> bool:
    return sys.platform == "win32"
