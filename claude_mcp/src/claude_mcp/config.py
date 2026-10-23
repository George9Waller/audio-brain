"""Settings, all from environment variables. The secrets come from the AWS secret `audio-brain` (helm/values.yaml)."""

import os
from dataclasses import dataclass, field
from pathlib import Path

# Claude's OAuth callback; registration refuses any other redirect URI (MCP_REDIRECT_URIS overrides, for testing)
CLAUDE_CALLBACKS = ("https://claude.ai/api/mcp/auth_callback", "https://claude.com/api/mcp/auth_callback")


@dataclass(frozen=True)
class Config:
    public_url: str  # where Funnel serves this pod, e.g. https://<node>.<tailnet>.ts.net; the OAuth issuer
    api_url: str  # the audio-brain API, ending /api/v1
    mcp_token: str  # the API's bearer token for Claude (principal "claude")
    passphrase_hash: str  # argon2 hash of the authorize page's passphrase (python -m claude_mcp.setup)
    totp_secret: str  # base32
    signing_key: str  # signs the pending authorization the login form carries
    db_path: Path  # OAuth clients and tokens; delete the file to revoke everything
    ui_dir: Path  # the built components (ui/dist)
    redirect_uris: tuple[str, ...] = field(default=CLAUDE_CALLBACKS)

    @property
    def mcp_url(self):
        return f"{self.public_url}/mcp"


def load():
    env = os.environ
    redirects = tuple(u.strip() for u in env.get("MCP_REDIRECT_URIS", "").split(",") if u.strip())
    return Config(
        public_url=env.get("MCP_PUBLIC_URL", "http://localhost:8001").rstrip("/"),
        api_url=env.get("AUDIO_BRAIN_API_URL", "http://audio-brain-api:8000/api/v1").rstrip("/"),
        mcp_token=env.get("MCP_TOKEN", ""),
        passphrase_hash=env.get("MCP_PASSPHRASE_HASH", ""),
        totp_secret=env.get("MCP_TOTP_SECRET", ""),
        signing_key=env.get("MCP_SIGNING_KEY", ""),
        db_path=Path(env.get("MCP_DB_PATH", "/data/oauth.sqlite3")),
        ui_dir=Path(env.get("MCP_UI_DIR", Path(__file__).resolve().parents[2] / "ui" / "dist")),
        redirect_uris=redirects or CLAUDE_CALLBACKS,
    )
