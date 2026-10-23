"""Make the MCP server's secrets: `uv run python -m claude_mcp.setup`.

Asks for a passphrase, then prints the four keys to add to the AWS secret `audio-brain` and the authenticator link
(otpauth://) to scan or paste into an authenticator app. Nothing is written to disk."""

import getpass
import json
import secrets

import pyotp
from argon2 import PasswordHasher


def main():
    passphrase = getpass.getpass("Passphrase for the connect page: ")
    if len(passphrase) < 12 or passphrase != getpass.getpass("Again: "):
        raise SystemExit("Passphrases didn't match, or shorter than 12 characters")
    totp = pyotp.random_base32()
    keys = {
        "MCP_TOKEN": secrets.token_urlsafe(32),
        "MCP_PASSPHRASE_HASH": PasswordHasher().hash(passphrase),
        "MCP_TOTP_SECRET": totp,
        "MCP_SIGNING_KEY": secrets.token_urlsafe(32),
    }
    print("\nAdd these to the AWS secret `audio-brain` (alongside the keys already there):\n")
    print(json.dumps(keys, indent=2))
    print("\nAuthenticator (scan as a QR code, or paste into the app):")
    print(pyotp.TOTP(totp).provisioning_uri(name="audio-brain", issuer_name="audio-brain"))


if __name__ == "__main__":
    main()
