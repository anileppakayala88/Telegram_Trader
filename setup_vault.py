"""setup_vault.py — One-time setup: encrypts .env into vault.enc, stores key in Windows Credential Manager.

Run once per machine:
    python setup_vault.py

To rotate credentials: edit .env, re-run this script.
To add a new secret mid-cycle: run with --merge to add without replacing existing vault.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SERVICE = "gb-trading"
_USERNAME = "vault"
_ENV_FILE = Path(".env")
_VAULT_FILE = Path("vault.enc")


def _parse_env(path: Path) -> dict[str, str]:
    secrets: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        secrets[k.strip()] = v.strip().strip('"').strip("'")
    return secrets


def main(merge: bool = False) -> None:
    try:
        import keyring
        from cryptography.fernet import Fernet
    except ImportError:
        print("ERROR: Run: pip install keyring cryptography")
        sys.exit(1)

    if not _ENV_FILE.exists():
        print(f"ERROR: {_ENV_FILE} not found.")
        sys.exit(1)

    new_secrets = _parse_env(_ENV_FILE)

    # Reuse existing key or generate a new one
    existing_key = keyring.get_password(_SERVICE, _USERNAME)
    if existing_key:
        key = existing_key.encode()
        print(f"Reusing existing vault key from Windows Credential Manager ({_SERVICE}/{_USERNAME}).")
    else:
        key = Fernet.generate_key()
        keyring.set_password(_SERVICE, _USERNAME, key.decode())
        print(f"New vault key stored in Windows Credential Manager ({_SERVICE}/{_USERNAME}).")

    fernet = Fernet(key)

    if merge and _VAULT_FILE.exists():
        existing_secrets: dict[str, str] = json.loads(fernet.decrypt(_VAULT_FILE.read_bytes()))
        # New values win over existing on conflict
        merged = {**existing_secrets, **new_secrets}
        print(f"Merging: {len(existing_secrets)} existing + {len(new_secrets)} from .env = {len(merged)} total.")
    else:
        merged = new_secrets

    encrypted = fernet.encrypt(json.dumps(merged).encode())
    _VAULT_FILE.write_bytes(encrypted)

    print(f"\nvault.enc written — {len(merged)} secret(s) encrypted.")
    print("\nKeys stored:")
    for k in sorted(merged):
        print(f"  {k}")
    print("\nNext steps:")
    print("  1. Verify the bot starts correctly: python bot.py (or main.py)")
    print("  2. Delete your .env file:  del .env")
    print("  3. Add vault.enc to .gitignore (already done if you pulled latest)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--merge", action="store_true", help="Merge .env into existing vault without replacing")
    args = parser.parse_args()
    main(merge=args.merge)
