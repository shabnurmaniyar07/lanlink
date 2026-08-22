"""The frozen v1 wire contract.

docs/protocol/v1.md is what the Android client will be written against, so a
change here is a change to somebody else's product. These tests fail if the
implementation drifts from the documents, and if the documents drift from the
implementation.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lanlink.api import create_app
from lanlink.discovery import SERVICE_TYPE
from lanlink.state import ALL_PERMISSIONS, PAIR_CODE_DIGITS, PAIR_MAX_FAILURES, PAIR_WINDOW_SECONDS, HubState

jsonschema = pytest.importorskip("jsonschema")

REPO = Path(__file__).resolve().parent.parent
PROTOCOL = REPO / "docs" / "protocol"
SCHEMA = json.loads((PROTOCOL / "schema.json").read_text(encoding="utf-8"))
SPEC = (PROTOCOL / "v1.md").read_text(encoding="utf-8")


def valid(name: str, payload: object) -> None:
    """Validate one response body against its $def in schema.json."""
    validator = jsonschema.Draft202012Validator({"$ref": f"#/$defs/{name}", **SCHEMA})
    errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.path))
    assert not errors, f"{name}: " + "; ".join(
        f"{'/'.join(str(part) for part in error.path)}: {error.message}" for error in errors
    )


@pytest.fixture
def node(tmp_path):
    """A paired node with a read-write share, a read-only share and some files."""
    root = tmp_path / "shared"
    root.mkdir()
    (root / "a.txt").write_bytes(b"hello")
    (root / "sub").mkdir()
    readonly = tmp_path / "readonly"
    readonly.mkdir()

    state = HubState(tmp_path / "settings.json")
    state.add_share(root, "Demo")
    state.add_share(readonly, "ReadOnly")
    shares = list(state.shares.values())
    shares[0].permissions = ALL_PERMISSIONS
    shares[1].permissions = "r"

    client = TestClient(create_app(state), raise_server_exceptions=False)
    code, _expires = state.start_pairing()
    token = client.post(
        "/v1/pair",
        json={"client_id": "client-12345678", "client_name": "Pixel 8", "pair_code": code},
    ).json()["token"]
    return {
        "state": state,
        "client": client,
        "auth": {"X-LanLink-Token": token},
        "share": shares[0].id,
        "readonly": shares[1].id,
        "root": root,
    }


# ------------------------------------------------------------------ inventory

DOCUMENTED_ROUTES = {
    ("GET", "/health"),
    ("GET", "/v1/device"),
    ("POST", "/v1/pair"),
    ("GET", "/v1/shares"),
    ("GET", "/v1/shares/{share_id}/list"),
    ("GET", "/v1/files/{share_id}"),
    ("PUT", "/v1/files/{share_id}"),
    ("GET", "/v1/shares/{share_id}/checksum"),
    ("POST", "/v1/uploads/{share_id}"),
    ("GET", "/v1/shares/{share_id}/partial"),
    ("POST", "/v1/shares/{share_id}/finalize"),
    ("POST", "/v1/shares/{share_id}/folders"),
    ("POST", "/v1/shares/{share_id}/rename"),
    ("DELETE", "/v1/shares/{share_id}/entries"),
    ("GET", "/v1/shares/{share_id}/properties"),
    ("POST", "/v1/operations"),
    ("DELETE", "/v1/pairings/{client_id}"),
    ("GET", "/v1/clipboard"),
    ("POST", "/v1/clipboard"),
    ("POST", "/v1/remote/mouse"),
    ("POST", "/v1/remote/media"),
}


def implemented_routes(app) -> set[tuple[str, str]]:
    found = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith(("/v1", "/health")):
            continue
        for method in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}:
            found.add((method, path))
    return found


def test_the_api_has_exactly_the_documented_endpoints(tmp_path) -> None:
    """A new endpoint is a protocol change and must be documented before it ships."""
    app = create_app(HubState(tmp_path / "settings.json"))
    assert implemented_routes(app) == DOCUMENTED_ROUTES


def test_every_endpoint_appears_in_the_specification() -> None:
    for _method, path in DOCUMENTED_ROUTES:
        assert path in SPEC, f"{path} is implemented but missing from docs/protocol/v1.md"


def test_no_endpoint_serves_a_user_interface(tmp_path) -> None:
    """LanLink's HTTP layer is transport. It must never grow a browser UI."""
    app = create_app(HubState(tmp_path / "settings.json"))
    paths = {getattr(route, "path", "") for route in app.routes}
    assert not {path for path in paths if path.startswith(("/static", "/ui", "/app"))}
    assert app.docs_url is None and app.redoc_url is None


def test_openapi_document_matches_the_implementation() -> None:
    """The committed openapi.yaml is generated; regenerate it when the API changes."""
    pytest.importorskip("yaml")
    result = subprocess.run(
        [sys.executable, str(REPO / "tools" / "export_openapi.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# --------------------------------------------------------------- response shapes


def test_health_and_device_shapes(node) -> None:
    valid("HealthResponse", node["client"].get("/health").json())
    body = node["client"].get("/v1/device").json()
    valid("DeviceResponse", body)
    assert body["pairing_armed"] is False, "pairing must be off again after a success"


def test_pair_response_shape(tmp_path) -> None:
    state = HubState(tmp_path / "settings.json")
    client = TestClient(create_app(state), raise_server_exceptions=False)
    code, _ = state.start_pairing()
    response = client.post(
        "/v1/pair", json={"client_id": "client-abcdefgh", "client_name": "Pixel 8", "pair_code": code}
    )
    assert response.status_code == 200
    valid("PairResponse", response.json())


def test_shares_and_listing_shapes(node) -> None:
    shares = node["client"].get("/v1/shares", headers=node["auth"]).json()
    valid("SharesResponse", shares)
    listing = node["client"].get(
        f"/v1/shares/{node['share']}/list", headers=node["auth"]
    ).json()
    valid("ListResponse", listing)
    assert [entry["kind"] for entry in listing["entries"]] == ["folder", "file"], "folders sort first"


def test_metadata_shapes(node) -> None:
    client, auth, share = node["client"], node["auth"], node["share"]
    get = lambda suffix, **params: client.get(  # noqa: E731 - one shape per line reads better
        f"/v1/shares/{share}/{suffix}", params=params, headers=auth
    ).json()
    valid("ChecksumResponse", get("checksum", path="a.txt"))
    valid("PartialResponse", get("partial", name="new.bin"))
    valid("Properties", get("properties"))
    valid("Properties", get("properties", path="a.txt"))


def test_write_operation_shapes(node) -> None:
    client, auth, share = node["client"], node["auth"], node["share"]
    uploaded = client.post(
        f"/v1/uploads/{share}", headers=auth, files={"file": ("up.bin", b"12345")}
    )
    valid("UploadResponse", uploaded.json())
    made = client.post(f"/v1/shares/{share}/folders", json={"path": "", "name": "New"}, headers=auth)
    valid("NamedResult", made.json())
    renamed = client.post(
        f"/v1/shares/{share}/rename", json={"path": "up.bin", "new_name": "up2.bin"}, headers=auth
    )
    valid("NamedResult", renamed.json())
    removed = client.request(
        "DELETE", f"/v1/shares/{share}/entries", params={"path": "up2.bin"}, headers=auth
    )
    valid("DeleteResponse", removed.json())


def test_resumable_upload_shapes_and_offsets(node) -> None:
    client, auth, share = node["client"], node["auth"], node["share"]
    body = b"abcdefghij"
    digest = hashlib.sha256(body).hexdigest()

    first = client.put(
        f"/v1/files/{share}",
        params={"name": "stream.bin", "offset": 0, "finalize": False},
        headers=auth,
        content=body[:4],
    )
    valid("StreamUploadResponse", first.json())
    assert first.json() == {"path": "stream.bin", "received": 4, "complete": False}

    status = client.get(f"/v1/shares/{share}/partial", params={"name": "stream.bin"}, headers=auth).json()
    assert status["received"] == 4 and status["complete"] is False

    ahead = client.put(
        f"/v1/files/{share}",
        params={"name": "stream.bin", "offset": 9, "finalize": False},
        headers=auth,
        content=b"x",
    )
    assert ahead.status_code == 409
    assert ahead.headers["X-LanLink-Received"] == "4", "the client is told where to resume from"

    done = client.put(
        f"/v1/files/{share}",
        params={"name": "stream.bin", "offset": 4, "finalize": True, "sha256": digest},
        headers=auth,
        content=body[4:],
    )
    valid("StreamUploadResponse", done.json())
    assert done.json()["complete"] is True and done.json()["bytes"] == len(body)
    assert (node["root"] / "stream.bin").read_bytes() == body


def test_operation_response_never_leaks_an_absolute_path(node) -> None:
    client, auth, share = node["client"], node["auth"], node["share"]
    client.post(f"/v1/shares/{share}/folders", json={"path": "", "name": "Out"}, headers=auth)
    body = client.post(
        "/v1/operations",
        json={
            "source_share_id": share,
            "source_path": "a.txt",
            "destination_share_id": share,
            "destination_path": "Out",
            "operation": "copy",
        },
        headers=auth,
    ).json()
    valid("OperationResponse", body)
    assert body["path"] == "Out/a.txt"
    assert not Path(body["path"]).is_absolute()
    assert str(node["root"]) not in body["path"]


def test_revoke_shape_and_self_only_rule(node) -> None:
    client, auth = node["client"], node["auth"]
    assert client.delete("/v1/pairings/somebody-else", headers=auth).status_code == 403
    valid("RevokeResponse", client.delete("/v1/pairings/client-12345678", headers=auth).json())
    assert client.get("/v1/shares", headers=auth).status_code == 401


# ----------------------------------------------------------------- status codes

DOCUMENTED_STATUS = {
    "unauthenticated": 401,
    "permission_denied": 403,
    "not_found": 404,
    "conflict": 409,
    "too_large": 413,
    "range_not_satisfiable": 416,
    "unprocessable": 422,
    "rate_limited": 429,
}


def test_documented_status_codes(node) -> None:
    client, auth, share, readonly = node["client"], node["auth"], node["share"], node["readonly"]

    assert client.get("/v1/shares").status_code == DOCUMENTED_STATUS["unauthenticated"]
    valid("Error", client.get("/v1/shares").json())

    denied = client.post(f"/v1/uploads/{readonly}", headers=auth, files={"file": ("x.bin", b"1")})
    assert denied.status_code == DOCUMENTED_STATUS["permission_denied"]
    valid("Error", denied.json())

    missing = client.get(f"/v1/shares/{share}/list", params={"path": "../.."}, headers=auth)
    assert missing.status_code == DOCUMENTED_STATUS["not_found"]

    client.post(f"/v1/uploads/{share}", headers=auth, files={"file": ("dup.bin", b"1")})
    duplicate = client.post(f"/v1/uploads/{share}", headers=auth, files={"file": ("dup.bin", b"1")})
    assert duplicate.status_code == DOCUMENTED_STATUS["conflict"]

    node["state"].max_upload_bytes = 2
    too_large = client.post(f"/v1/uploads/{share}", headers=auth, files={"file": ("big.bin", b"12345")})
    assert too_large.status_code == DOCUMENTED_STATUS["too_large"]
    node["state"].max_upload_bytes = 0

    past_end = client.get(
        f"/v1/files/{share}", params={"path": "a.txt"}, headers={**auth, "Range": "bytes=99-"}
    )
    assert past_end.status_code == DOCUMENTED_STATUS["range_not_satisfiable"]
    assert past_end.headers["Content-Range"] == "bytes */5"

    bad_body = client.post("/v1/pair", json={"client_id": "x", "client_name": "n", "pair_code": "12"})
    assert bad_body.status_code == DOCUMENTED_STATUS["unprocessable"]
    valid("ValidationError", bad_body.json())


def test_every_documented_status_code_appears_in_the_specification() -> None:
    for code in DOCUMENTED_STATUS.values():
        assert str(code) in SPEC, f"status {code} is used but not documented"


def test_pairing_failure_codes_are_distinct_and_documented(tmp_path) -> None:
    """An Android client shows a different message for each of these."""
    from lanlink.api import PAIR_FAILURE_STATUS

    assert PAIR_FAILURE_STATUS == {
        "not_armed": 409,
        "rate_limited": 429,
        "locked_out": 429,
        "invalid_code": 403,
        "declined": 403,
    }
    for reason in PAIR_FAILURE_STATUS:
        assert reason in SPEC, f"pairing outcome {reason} is not documented"

    state = HubState(tmp_path / "settings.json")
    client = TestClient(create_app(state), raise_server_exceptions=False)
    cold = client.post(
        "/v1/pair", json={"client_id": "client-12345678", "client_name": "N", "pair_code": "12345678"}
    )
    assert cold.status_code == 409, "not in pairing mode is 409, distinct from a wrong code"


# ------------------------------------------------------------------ download


def test_download_headers_are_what_a_resuming_client_needs(node) -> None:
    client, auth, share = node["client"], node["auth"], node["share"]

    whole = client.get(f"/v1/files/{share}", params={"path": "a.txt"}, headers=auth)
    assert whole.status_code == 200
    assert whole.headers["accept-ranges"] == "bytes"
    assert whole.headers["content-length"] == "5"
    assert 'filename="a.txt"' in whole.headers["content-disposition"]

    part = client.get(
        f"/v1/files/{share}", params={"path": "a.txt"}, headers={**auth, "Range": "bytes=2-"}
    )
    assert part.status_code == 206
    assert part.headers["content-range"] == "bytes 2-4/5"
    assert part.headers["content-length"] == "3"
    assert part.content == b"llo"


def test_a_range_the_server_cannot_honour_falls_back_to_the_whole_file(node) -> None:
    """Documented behaviour: a client that asked to resume must check the status."""
    client, auth, share = node["client"], node["auth"], node["share"]
    # Suffix ranges, multi-ranges, other units and nonsense all fall back to the
    # whole file. Crucially the body is never multipart/byteranges, which a
    # client resuming a download would write straight into the file.
    for header in ("items=2-", "bytes=-3", "bytes=1-2,4-5", "bytes=abc-", "bogus", "bytes=3-1"):
        response = client.get(
            f"/v1/files/{share}", params={"path": "a.txt"}, headers={**auth, "Range": header}
        )
        assert response.status_code == 200, header
        assert response.content == b"hello", header
        assert "multipart" not in response.headers.get("content-type", ""), header


def test_a_closed_range_returns_exactly_what_was_asked_for(node) -> None:
    client, auth, share = node["client"], node["auth"], node["share"]

    exact = client.get(
        f"/v1/files/{share}", params={"path": "a.txt"}, headers={**auth, "Range": "bytes=1-3"}
    )
    assert exact.status_code == 206
    assert exact.headers["content-range"] == "bytes 1-3/5"
    assert exact.headers["content-length"] == "3"
    assert exact.content == b"ell"

    # An end past EOF is clamped rather than refused.
    clamped = client.get(
        f"/v1/files/{share}", params={"path": "a.txt"}, headers={**auth, "Range": "bytes=1-99"}
    )
    assert clamped.status_code == 206
    assert clamped.headers["content-range"] == "bytes 1-4/5"
    assert clamped.content == b"ello"

    from_zero = client.get(
        f"/v1/files/{share}", params={"path": "a.txt"}, headers={**auth, "Range": "BYTES=0-"}
    )
    assert from_zero.status_code == 206, "the unit is case-insensitive"
    assert from_zero.headers["content-range"] == "bytes 0-4/5"


# ------------------------------------------------------------------ discovery


def test_mdns_service_type_and_txt_keys_are_frozen(tmp_path, monkeypatch) -> None:
    from lanlink import discovery as module

    assert SERVICE_TYPE == "_lanlink._tcp.local."
    assert SERVICE_TYPE in SPEC

    captured = {}

    class FakeInfo:
        def __init__(self, service_type, name, addresses, port, properties, server):
            captured.update(properties)
            captured["_service_type"] = service_type
            captured["_server"] = server

    monkeypatch.setattr(module, "ServiceInfo", FakeInfo)
    monkeypatch.setattr(module, "local_ipv4_addresses", lambda: [b"\x7f\x00\x00\x01"])
    def no_multicast():
        raise RuntimeError("tests never join a multicast group")

    monkeypatch.setattr(module, "acquire_zeroconf", no_multicast)

    state = HubState(tmp_path / "settings.json")
    service = module.DiscoveryService(state, port=8765, scheme="https")
    # Registration is stubbed out and will raise; only the TXT record matters here.
    with contextlib.suppress(RuntimeError):
        service.start()

    txt = {key: value for key, value in captured.items() if not key.startswith("_")}
    valid("MdnsTxt", txt)
    assert set(txt) == {"id", "name", "api", "platform", "version", "scheme", "fp"}
    assert txt["api"] == "v1"
    assert len(txt["fp"]) <= 32, "the TXT carries a fingerprint hint, not the full pin"


def test_discovery_ignores_a_service_without_a_device_id() -> None:
    """An id is the only field that makes a record usable; everything else has a default."""
    from lanlink.discovery import device_from_service_info

    class Info:
        name = "Thing._lanlink._tcp.local."
        port = 8765
        addresses = [b"\xc0\xa8\x01\x14"]
        properties: dict = {b"name": b"Thing"}

    assert device_from_service_info(Info()) is None


# ------------------------------------------------------------------- pairing


def test_pairing_constants_match_the_specification() -> None:
    assert PAIR_CODE_DIGITS == 8
    assert PAIR_WINDOW_SECONDS == 120
    assert PAIR_MAX_FAILURES == 5
    for value in ("8-digit", "120", "five"):
        assert value in SPEC.lower(), f"{value} is a frozen pairing rule and must be documented"


def test_invite_url_round_trips(tmp_path) -> None:
    from lanlink.invite import Invite, parse_invite

    invite = Invite(
        host="192.168.1.20", port=8765, code="12345678", device_id="dev-1", name="PC", fingerprint="ab" * 32
    )
    url = invite.to_url()
    assert url.startswith("lanlink://pair?")
    parsed = parse_invite(url)
    assert (parsed.host, parsed.port, parsed.code, parsed.fingerprint) == (
        "192.168.1.20",
        8765,
        "12345678",
        "ab" * 32,
    )
    assert "lanlink://pair" in SPEC


# ---------------------------------------------------------------- path rules


@pytest.mark.parametrize(
    "path",
    ["../secret.txt", "..\\secret.txt", "/etc/passwd", "C:\\Windows\\win.ini", "//server/share", "a/../../b"],
)
def test_paths_that_leave_the_share_are_refused(node, path: str) -> None:
    response = node["client"].get(
        f"/v1/shares/{node['share']}/list", params={"path": path}, headers=node["auth"]
    )
    assert response.status_code == 404


@pytest.mark.parametrize("name", ["../evil", "a/b", "a\\b", "CON", "PRN", "trailing.", " leading", "x" * 256])
def test_names_that_are_not_one_safe_leaf_are_refused(node, name: str) -> None:
    response = node["client"].post(
        f"/v1/shares/{node['share']}/folders", json={"path": "", "name": name}, headers=node["auth"]
    )
    assert response.status_code in {409, 422}


def test_partial_files_are_never_listed(node) -> None:
    (node["root"] / "ghost.bin.lanlink-part").write_bytes(b"half")
    entries = node["client"].get(
        f"/v1/shares/{node['share']}/list", headers=node["auth"]
    ).json()["entries"]
    assert not [entry for entry in entries if "lanlink-part" in entry["name"]]


# ---------------------------------------------------------------- permissions


def test_read_is_implicit_and_cannot_be_removed(node) -> None:
    """set_share_permissions always re-adds r, so a shared folder is always readable."""
    state, share = node["state"], node["share"]
    for requested, expected in [("", "r"), ("w", "rw"), ("d", "rd"), ("wd", "rwd"), ("RWD", "rwd")]:
        assert state.set_share_permissions(share, requested).permissions == expected


def test_the_permission_gate_matrix(node) -> None:
    """Exactly which endpoints refuse, and with which flag missing."""
    client, auth, share, root = node["client"], node["auth"], node["share"], node["root"]
    node["state"].set_share_permissions(share, "r")  # read only

    write_attempts = [
        client.post(f"/v1/uploads/{share}", headers=auth, files={"file": ("n.bin", b"1")}),
        client.put(f"/v1/files/{share}", params={"name": "n.bin"}, headers=auth, content=b"1"),
        client.post(f"/v1/shares/{share}/finalize", params={"name": "n.bin"}, headers=auth),
        client.post(f"/v1/shares/{share}/folders", json={"path": "", "name": "N"}, headers=auth),
        client.post(f"/v1/shares/{share}/rename", json={"path": "a.txt", "new_name": "b.txt"}, headers=auth),
    ]
    assert [response.status_code for response in write_attempts] == [403] * 5

    deletion = client.request("DELETE", f"/v1/shares/{share}/entries", params={"path": "a.txt"}, headers=auth)
    assert deletion.status_code == 403

    # Reading is never gated, and nothing above changed the folder.
    for response in (
        client.get(f"/v1/shares/{share}/list", headers=auth),
        client.get(f"/v1/files/{share}", params={"path": "a.txt"}, headers=auth),
        client.get(f"/v1/shares/{share}/checksum", params={"path": "a.txt"}, headers=auth),
        client.get(f"/v1/shares/{share}/properties", params={"path": "a.txt"}, headers=auth),
        client.get(f"/v1/shares/{share}/partial", params={"name": "n.bin"}, headers=auth),
    ):
        assert response.status_code == 200
    assert sorted(item.name for item in root.iterdir()) == ["a.txt", "sub"]


def test_move_needs_delete_on_the_source_and_write_on_the_destination(node) -> None:
    client, auth, share = node["client"], node["auth"], node["share"]
    node["state"].set_share_permissions(share, "rw")  # write, but no delete
    refused = client.post(
        "/v1/operations",
        json={
            "source_share_id": share,
            "source_path": "a.txt",
            "destination_share_id": share,
            "destination_path": "sub",
            "operation": "move",
        },
        headers=auth,
    )
    assert refused.status_code == 403
    assert (node["root"] / "a.txt").exists(), "a refused move must not have deleted anything"


# ------------------------------------------------------------------- identity


def test_share_ids_and_tokens_have_the_documented_shape(node) -> None:
    shares = node["client"].get("/v1/shares", headers=node["auth"]).json()["shares"]
    for share in shares:
        assert share["id"].startswith("share_") and len(share["id"]) == len("share_") + 12
    token = node["auth"]["X-LanLink-Token"]
    assert len(token) == 43, "secrets.token_urlsafe(32) is 43 characters"
    assert "43 URL-safe base64 characters" in SPEC


def test_a_token_is_never_stored_in_the_clear(node) -> None:
    settings = node["state"].settings_path.read_text(encoding="utf-8")
    assert node["auth"]["X-LanLink-Token"] not in settings
    assert "token_hash" in settings
