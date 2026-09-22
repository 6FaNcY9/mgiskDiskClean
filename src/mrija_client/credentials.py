from __future__ import annotations

import os
from pathlib import Path


class CredentialError(ValueError):
    """Credential configuration is invalid or unavailable."""


def read_credential(name: str) -> str:
    file_name = f"{name}_FILE"
    if file_name not in os.environ:
        return os.environ.get(name, "")

    direct = os.environ.get(name, "")
    if direct:
        raise CredentialError(f"conflicting {name} and {file_name}")

    try:
        value = Path(os.environ[file_name]).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CredentialError(f"cannot read {file_name}") from exc

    if value.endswith("\n"):
        value = value[:-1]
    if not value:
        raise CredentialError(f"empty {file_name}")
    return value
