"""vault.py — Load encrypted credentials from vault.enc via Windows Credential Manager."""
from __future__ import annotations

import json
import os
from pathlib import Path

_SERVICE = "gb-trading"
_USERNAME = "vault"
_VAULT_FILE = Path(__file__).parent / "vault.enc"


def load_secrets(vault_file: Path | None = None) -> None:
    """Decrypt vault and inject all keys into os.environ (skips already-set vars)."""
    try:
        import keyring
        from cryptography.fernet import Fernet
    except ImportError as e:
        raise ImportError("Run: pip install keyring cryptography") from e

    key = keyring.get_password(_SERVICE, _USERNAME)
    if not key:
        raise RuntimeError(
            "Vault key not found in Windows Credential Manager.\n"
            "Run: python setup_vault.py"
        )

    vf = vault_file or _VAULT_FILE
    if not vf.exists():
        raise FileNotFoundError(
            f"vault.enc not found at {vf}.\n"
            "Run: python setup_vault.py"
        )

    secrets: dict[str, str] = json.loads(Fernet(key.encode()).decrypt(vf.read_bytes()))
    for k, v in secrets.items():
        os.environ.setdefault(k, v)
