import base64
import hashlib
import json
import secrets
from urllib.parse import parse_qs, urlparse

import httpx
import pyotp
import pytest
from argon2 import PasswordHasher
from starlette.testclient import TestClient

from claude_mcp.config import Config
from claude_mcp.server import build

CALLBACK = "https://claude.ai/api/mcp/auth_callback"
PUBLIC = "https://mcp.example.ts.net"
LIST = {"key": "ctx-7", "kind": "mix", "section": "lists", "title": "Late night", "subtitle": "slow and deep",
        "context": {"id": 7, "name": "Late night", "hue": 210.0, "family": None}, "items": [
            {"kind": "track", "track_id": 1, "artist": "Burial", "title": "Archangel", "reason": "you play it at night",
             "novel": False, "hue": 210.0, "styles": ["Dubstep"], "verdict": "yes", "sound": {"bpm": 139.6, "camelot": "8A", "energy": 0.41,
             "instrumental": 0.8, "moods": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6], "shape": [0.9, 0.1, 0.5, 0.5, 0.2, 0.7]},
             "length_ms": 238000, "artist_new": False, "context_name": "Late night"},
            {"kind": "track", "track_id": 2, "artist": "Four Tet", "title": "Lush", "novel": True, "sound": None,
             "length_ms": 302000, "artist_new": True, "context_name": None}]}
RELEASES = {"key": "releases", "kind": "releases", "section": "discover", "title": "New releases", "subtitle": "from artists you play",
            "context": None, "items": [{"kind": "release", "release_id": 9, "title": "Singularity", "artist": "Jon Hopkins",
                                        "detail": "Album · 4 May 2018", "reason": "artist you play", "url": "https://tidal.com/browse/album/1"}]}


@pytest.fixture
def env(tmp_path):
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "list.html").write_text("<html>list</html>")
    totp = pyotp.random_base32()
    config = Config(public_url=PUBLIC, api_url="http://api/api/v1", mcp_token="claude-token",
                    passphrase_hash=PasswordHasher().hash("correct horse battery"), totp_secret=totp,
                    signing_key="signing", db_path=tmp_path / "oauth.sqlite3", ui_dir=ui)
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer claude-token"
        if request.url.path == "/api/v1/lists":
            return httpx.Response(200, json={"lists": [LIST, RELEASES]})
        if request.method == "GET" and request.url.path in ("/api/v1/mixes/ctx-7", "/api/v1/mixes/releases"):
            return httpx.Response(200, json=LIST if "ctx-7" in request.url.path else RELEASES)
        if request.url.path == "/api/v1/mixes/ctx-7/sync":
            assert json.loads(request.content) == {"on": True, "pinned": True}
            return httpx.Response(200, json={"sync": True, "pinned": True, "tidal_url": "https://tidal.com/browse/playlist/own"})
        if request.url.path == "/api/v1/mixes/ctx-7/play":
            return httpx.Response(200, json={"tidal_url": "https://tidal.com/browse/playlist/abc"})
        return httpx.Response(404, json={"detail": "Not Found"})

    app = build(config, httpx.MockTransport(handler)).streamable_http_app(host="0.0.0.0")
    with TestClient(app, base_url=PUBLIC) as client:
        yield client, pyotp.TOTP(totp), calls


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def register(client, redirect=CALLBACK):
    return client.post("/register", json={"redirect_uris": [redirect], "client_name": "Claude",
                                          "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"]})


def login_form(client, client_id, challenge):
    response = client.get("/authorize", params={"response_type": "code", "client_id": client_id, "redirect_uri": CALLBACK,
                                                "code_challenge": challenge, "code_challenge_method": "S256", "state": "xyz"},
                          follow_redirects=False)
    assert response.status_code == 302
    login = urlparse(response.headers["location"])
    assert login.path == "/login"
    return parse_qs(login.query)["request"][0]


def connect(client, totp):
    client_id = register(client).json()["client_id"]
    verifier, challenge = pkce()
    request = login_form(client, client_id, challenge)
    response = client.post("/login", data={"request": request, "passphrase": "correct horse battery", "code": totp.now()},
                           follow_redirects=False)
    assert response.status_code == 302
    back = urlparse(response.headers["location"])
    assert f"{back.scheme}://{back.netloc}{back.path}" == CALLBACK
    query = parse_qs(back.query)
    assert query["state"] == ["xyz"]
    tokens = client.post("/token", data={"grant_type": "authorization_code", "code": query["code"][0], "redirect_uri": CALLBACK,
                                         "client_id": client_id, "code_verifier": verifier})
    assert tokens.status_code == 200, tokens.text
    return client_id, tokens.json()


def test_metadata_points_at_the_public_url(env):
    client, _, _ = env
    meta = client.get("/.well-known/oauth-authorization-server").json()
    assert meta["authorization_endpoint"] == f"{PUBLIC}/authorize"
    assert "S256" in meta["code_challenge_methods_supported"]
    resource = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert resource["resource"] == f"{PUBLIC}/mcp"
    unauthorised = client.post("/mcp", json={})
    assert unauthorised.status_code == 401
    assert "resource_metadata" in unauthorised.headers["www-authenticate"]


def test_registration_refuses_other_redirects(env):
    client, _, _ = env
    assert register(client, "https://evil.example/callback").status_code == 400
    assert register(client).status_code == 201


def test_wrong_code_or_passphrase_is_refused_and_rate_limited(env):
    client, totp, _ = env
    client_id = register(client).json()["client_id"]
    request = login_form(client, client_id, pkce()[1])
    wrong_code = client.post("/login", data={"request": request, "passphrase": "correct horse battery", "code": "000000"})
    assert wrong_code.status_code == 401
    wrong_pass = client.post("/login", data={"request": request, "passphrase": "nope", "code": totp.now()})
    assert wrong_pass.status_code == 401
    for _ in range(3):
        client.post("/login", data={"request": request, "passphrase": "nope", "code": "000000"})
    blocked = client.post("/login", data={"request": request, "passphrase": "correct horse battery", "code": totp.now()})
    assert blocked.status_code == 429


def test_rate_limit_ignores_forged_forwarded_addresses(env):
    client, totp, _ = env
    client_id = register(client).json()["client_id"]
    request = login_form(client, client_id, pkce()[1])
    for n in range(5):  # a new made-up address each try; Funnel appends the real one last
        client.post("/login", data={"request": request, "passphrase": "nope", "code": "000000"},
                    headers={"X-Forwarded-For": f"10.0.0.{n}, 203.0.113.7"})
    blocked = client.post("/login", data={"request": request, "passphrase": "correct horse battery", "code": totp.now()},
                          headers={"X-Forwarded-For": "10.0.0.99, 203.0.113.7"})
    assert blocked.status_code == 429


def test_tampered_login_request_is_refused(env):
    client, totp, _ = env
    client_id = register(client).json()["client_id"]
    request = login_form(client, client_id, pkce()[1])
    body, mac = request.rsplit(".", 1)
    data = json.loads(base64.urlsafe_b64decode(body))
    forged = base64.urlsafe_b64encode(json.dumps({**data, "redirect_uri": "https://evil.example/"}).encode()).decode()
    response = client.post("/login", data={"request": f"{forged}.{mac}", "passphrase": "correct horse battery", "code": totp.now()},
                           follow_redirects=False)
    assert response.status_code == 400


def test_pkce_is_checked(env):
    client, totp, _ = env
    client_id = register(client).json()["client_id"]
    request = login_form(client, client_id, pkce()[1])
    response = client.post("/login", data={"request": request, "passphrase": "correct horse battery", "code": totp.now()},
                           follow_redirects=False)
    code = parse_qs(urlparse(response.headers["location"]).query)["code"][0]
    tokens = client.post("/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": CALLBACK,
                                         "client_id": client_id, "code_verifier": pkce()[0]})
    assert tokens.status_code == 400


def test_refresh_rotates_tokens(env):
    client, totp, _ = env
    client_id, tokens = connect(client, totp)
    refreshed = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": client_id})
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["access_token"] != tokens["access_token"]
    again = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": client_id})
    assert again.status_code == 400


def mcp(client, token, method, params=None, session=None):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"}
    if session:
        headers["mcp-session-id"] = session
    response = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
    assert response.status_code == 200, response.text
    text = response.text
    body = json.loads(text.split("data: ", 1)[1].split("\n")[0]) if text.startswith(("event:", "data:")) else response.json()
    return body, response.headers.get("mcp-session-id")


def call_tool(client, token, name, arguments):
    init, session = mcp(client, token, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {
        "extensions": {"io.modelcontextprotocol/ui": {"mimeTypes": ["text/html;profile=mcp-app"]}}},
        "clientInfo": {"name": "test", "version": "1"}})
    assert "result" in init, init
    client.post("/mcp", headers={"Authorization": f"Bearer {token}", "mcp-session-id": session or "",
                                 "Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    if name is None:
        return mcp(client, token, "tools/list", session=session)[0]["result"]
    return mcp(client, token, "tools/call", {"name": name, "arguments": arguments}, session=session)[0]["result"]


def test_tools_over_mcp(env):
    client, totp, calls = env
    _, tokens = connect(client, totp)
    token = tokens["access_token"]

    tools = {t["name"]: t for t in call_tool(client, token, None, None)["tools"]}
    assert tools["show_list"]["_meta"]["ui"]["resourceUri"] == "ui://audio-brain/list.html"
    assert tools["show_list"]["annotations"]["readOnlyHint"] is True

    index = call_tool(client, token, "show_list", {})
    assert index["structuredContent"]["lists"][0] == {"key": "ctx-7", "title": "Late night", "subtitle": "slow and deep",
                                                      "section": "lists", "kind": "mix", "family": "",
                                                      "sync": False, "tracks": 2, "count": "2 tracks", "novel": 0.5, "hue": 210.0,
                                                      "hues": [210.0, None]}
    assert index["structuredContent"]["lists"][1]["count"] == "1 release"

    shown = call_tool(client, token, "show_list", {"key": "ctx-7"})
    text = shown["content"][0]["text"]
    assert "1. Burial – Archangel (#1): 140 BPM, 8A, energy 0.41, instrumental; Late night, verdict yes; you play it at night" in text
    assert "2. Four Tet – Lush (#2): new to you, new artist" in text
    assert "Mostly Burial, with Four Tet; 1 artist new to you." in text and "9 m" in text
    data = shown["structuredContent"]
    assert (data["duration"], data["new"], data["contexts"]) == ("9 m", 1, [{"name": "Late night", "hue": 210.0, "tracks": 1}])
    assert data["kept"] is False and data["can_keep"] is True
    assert shown["structuredContent"]["tracks"][0]["camelot"] == "8A"
    assert shown["structuredContent"]["tracks"][1]["bpm"] is None
    first = shown["structuredContent"]["tracks"][0]
    assert first["moods"] == [0.9, 0.1, 0.5, 0.5, 0.2, 0.7] and first["vocal"] is False and first["verdict"] == "yes"

    releases = call_tool(client, token, "show_list", {"key": "releases"})
    assert "1. Jon Hopkins – Singularity (release)" in releases["content"][0]["text"]
    assert releases["structuredContent"]["others"][0]["url"] == "https://tidal.com/browse/album/1"

    missing = call_tool(client, token, "show_list", {"key": "nope"})
    assert missing["isError"] is True

    played = call_tool(client, token, "play", {"key": "ctx-7"})
    assert played["structuredContent"]["tidal_url"] == "https://tidal.com/browse/playlist/abc"
    assert calls[-1].method == "POST"

    kept = call_tool(client, token, "keep", {"key": "ctx-7"})
    assert kept["structuredContent"] == {"key": "ctx-7", "kept": True, "tidal_url": "https://tidal.com/browse/playlist/own"}
    assert call_tool(client, token, "keep", {"key": "releases"})["isError"] is True
