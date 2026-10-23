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


STATS = {"group": "label", "measure": "weighted", "granularity": "year", "start": "2022-01-01", "end": None, "filters": {},
         "total": {"plays": 300, "weighted": 250.0}, "unassigned": {"plays": 20, "weighted": 15.0},
         "rows": [{"key": "5", "label": "Ninja Tune", "plays": 120, "weighted": 100.0, "share": 0.4},
                  {"key": "9", "label": "Warp", "plays": 60, "weighted": 50.0, "share": 0.2}],
         "periods": ["2022-01-01", "2023-01-01"], "totals": [150.0, 100.0],
         "series": [{"key": "5", "label": "Ninja Tune", "values": [90.0, 10.0]}, {"key": "9", "label": "Warp", "values": [20.0, 30.0]}]}
STORY = {"id": 1, "line": "Burial – Archangel (#1)", "name": "Archangel", "artist": "Burial", "artist_id": 3, "album": "Untrue",
         "label": "Hyperdub", "released": "2007-11-05", "plays": 40, "weighted_plays": 31.5, "first_played": "2016-02-01T22:10+00:00",
         "last_played": "2026-10-01T23:00+01:00", "sound": {"bpm": 139.6, "key": "C", "scale": "minor", "camelot": "5A", "energy": 0.4, "instrumental": 0.3},
         "context": {"id": 7, "name": "Late night", "hue": 210.0}, "styles": ["Dubstep"], "countries": [{"key": "GB", "label": "GB", "plays": 38, "weighted": 30}],
         "months": [{"month": "2016-02-01", "plays": 5, "weighted": 4}, {"month": "2016-03-01", "plays": 0, "weighted": 0}],
         "hours": [0] * 23 + [12], "verdicts": [{"verdict": "yes", "given_at": "2026-10-01", "where": "Late night", "source": "app", "note": ""}],
         "favourites": ["tidal"], "lists": [{"key": "ctx-7", "title": "Late night", "kind": "mix"}],
         "recommendations": {"suggested": 2, "played": 1, "not_played": 1, "pending": 0, "played_again": 1, "sources": {"mix": 2}}}
ARTIST = {"id": 3, "name": "Burial", "country": "GB", "area": "London", "kind": "Person", "active_from": 2005, "active_to": None,
          "language": "", "genres": [], "plays": 90, "weighted_plays": 70.0, "first_played": "2016-02-01T22:10+00:00",
          "last_played": "2026-10-01T23:00+01:00", "tracks": 12, "tracks_played": 8,
          "top_tracks": [{"track_id": 1, "title": "Archangel", "plays": 40, "weighted": 31.5}], "months": [], "hours": [0] * 24,
          "labels": [{"key": "2", "label": "Hyperdub", "plays": 80, "weighted": 60}], "styles": [], "contexts": [],
          "similar": [{"name": "Kode9", "match": 0.8, "artist_id": None, "plays": 0}], "verdicts": {}, "favourite": True,
          "favourite_tracks": 0, "remixes": 0, "releases": []}
ROUTES = {
    "/api/v1/history/stats": lambda r: {**STATS, "group": r.url.params.get("group", "artist")},
    "/api/v1/tracks/1/story": lambda r: STORY,
    "/api/v1/artists/3/story": lambda r: ARTIST,
    "/api/v1/search": lambda r: {"tracks": [], "artists": [{"id": 3, "name": "Burial", "tracks": 12}], "contexts": []},
    "/api/v1/query": lambda r: {"columns": ["n"], "rows": [[1], [2]], "truncated": True, "ms": 4},
    "/api/v1/schema": lambda r: {"text": "# guide " + r.url.params.get("tables", "")},
}


@pytest.fixture
def env(tmp_path):
    ui = tmp_path / "ui"
    ui.mkdir()
    for name in ("list", "chart", "story"):
        (ui / f"{name}.html").write_text(f"<html>{name}</html>")
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
        if request.url.path in ROUTES:
            return httpx.Response(200, json=ROUTES[request.url.path](request))
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


def test_reasoning_tools_over_mcp(env):
    client, totp, calls = env
    token = connect(client, totp)[1]["access_token"]
    tools = {t["name"]: t for t in call_tool(client, token, None, None)["tools"]}
    assert tools["stats"]["_meta"]["ui"]["resourceUri"] == "ui://audio-brain/chart.html"
    assert tools["artist"]["_meta"]["ui"]["resourceUri"] == "ui://audio-brain/story.html"
    assert all(tools[n]["annotations"]["readOnlyHint"] for n in ("stats", "sql", "schema", "track_story", "listens", "now"))

    stats = call_tool(client, token, "stats", {"group": "label", "granularity": "year", "start": "2022-01-01", "country": "FR"})
    request = calls[-1]
    assert (request.url.params["country"], request.url.params["measure"], "artist" in request.url.params) == ("FR", "weighted", False)
    text = stats["content"][0]["text"]
    assert "| Ninja Tune (#5) | 120 | 100 | 40.0% |" in text and "Plays with no label yet: 15" in text
    assert "| Ninja Tune | 90 | 10 |" in text and "| 2022 | 2023 |" in text
    assert stats["structuredContent"]["view"] == "lines"
    assert call_tool(client, token, "stats", {"group": "weekday_hour"})["structuredContent"]["view"] == "heatmap"

    story = call_tool(client, token, "track_story", {"track_id": 1})
    assert story["structuredContent"]["view"] == "track"
    assert "Sound: 140 BPM, 5A, energy 0.40, vocal." in story["content"][0]["text"]
    assert "Suggested 2 times (mix 2): played after 1" in story["content"][0]["text"]

    artist = call_tool(client, token, "artist", {"artist": "burial"})  # by name, through search
    assert artist["structuredContent"]["view"] == "artist" and "**Burial** (#3): Person, London" in artist["content"][0]["text"]

    sql = call_tool(client, token, "sql", {"query": "select n from x"})
    assert "| 1 |" in sql["content"][0]["text"] and "cut at the limit" in sql["content"][0]["text"]
    assert call_tool(client, token, "schema", {"tables": ["history_listen"]})["content"][0]["text"] == "# guide history_listen"
