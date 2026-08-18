"""Pairing invitations: one link that carries address, code and certificate pin.

A LanLink invite is a ``lanlink://pair`` URL. It can be shown as a QR code for a
phone camera, or copied and pasted between two computers. It carries the
certificate fingerprint, so the receiving device can pin the right identity
instead of trusting whatever answers on that address.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, quote, urlencode, urlparse

SCHEME = "lanlink"
ACTION = "pair"


class InvalidInvite(ValueError):
    pass


@dataclass
class Invite:
    host: str
    port: int
    code: str
    device_id: str = ""
    name: str = ""
    fingerprint: str = ""
    scheme: str = "https"

    @property
    def base_url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"

    def to_url(self) -> str:
        fields = {
            "host": self.host,
            "port": str(self.port),
            "code": self.code,
            "id": self.device_id,
            "name": self.name,
            "fp": self.fingerprint,
            "scheme": self.scheme,
        }
        query = urlencode({key: value for key, value in fields.items() if value}, quote_via=quote)
        return f"{SCHEME}://{ACTION}?{query}"


def parse_invite(text: str) -> Invite:
    """Accept a lanlink:// invite, or a bare host:port typed by hand."""
    candidate = (text or "").strip()
    if not candidate:
        raise InvalidInvite("Paste a LanLink invite or an address first.")

    if candidate.lower().startswith(f"{SCHEME}://"):
        parsed = urlparse(candidate)
        if parsed.netloc and parsed.netloc.lower() != ACTION:
            raise InvalidInvite("That LanLink link is not a pairing invite.")
        values = {key: value[0] for key, value in parse_qs(parsed.query).items() if value}
        host = values.get("host", "").strip()
        port_text = values.get("port", "").strip()
        if not host or not port_text.isdigit():
            raise InvalidInvite("That invite is missing the device address.")
        return Invite(
            host=host,
            port=int(port_text),
            code=values.get("code", "").strip(),
            device_id=values.get("id", "").strip(),
            name=values.get("name", "").strip(),
            fingerprint=values.get("fp", "").strip(),
            scheme=values.get("scheme", "https").strip() or "https",
        )

    scheme = "https"
    if candidate.startswith(("http://", "https://")):
        parsed = urlparse(candidate)
        scheme = parsed.scheme
        candidate = parsed.netloc
    host, _, port_text = candidate.partition(":")
    host = host.strip("/")
    if not host:
        raise InvalidInvite("That does not look like a device address.")
    try:
        port = int(port_text) if port_text else 8765
    except ValueError as error:
        raise InvalidInvite("The port must be a number.") from error
    return Invite(host=host, port=port, code="", scheme=scheme)


def qr_matrix(payload: str) -> list[list[bool]]:
    """Render the invite to a boolean matrix a UI can paint however it likes."""
    import segno

    code = segno.make(payload, error="m")
    return [[bool(cell) for cell in row] for row in code.matrix]
