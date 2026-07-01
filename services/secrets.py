"""Pluggable secrets / KMS management (Requirement 19 hardening).

Secrets are read through a `SecretProvider`. The default `EnvSecretProvider`
reads environment variables / application settings. Managed providers (AWS
Secrets Manager, HashiCorp Vault) activate when their optional SDKs are present
and ``SECRET_PROVIDER`` is set. `require_strong_secrets` enforces a minimum key
length so weak keys can't reach production.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Optional

from config.settings import settings
from utils.logging import get_logger

logger = get_logger(__name__)

MIN_KEY_LENGTH = 32


class WeakSecretError(RuntimeError):
    """Raised when a required secret is too weak for the current environment."""


class SecretProvider(ABC):
    """Interface for secret backends."""

    @abstractmethod
    def get(self, name: str, default: Optional[str] = None) -> Optional[str]:
        """Return the secret value, or default if unset."""


class EnvSecretProvider(SecretProvider):
    """Reads secrets from environment variables, falling back to settings."""

    def get(self, name: str, default: Optional[str] = None) -> Optional[str]:
        if name in os.environ:
            return os.environ[name]
        attr = getattr(settings, name.lower(), None)
        return attr if attr is not None else default


class AwsSecretsManagerProvider(SecretProvider):  # pragma: no cover - needs boto3 + AWS
    """Reads secrets from AWS Secrets Manager."""

    def __init__(self, region: Optional[str] = None) -> None:
        import boto3

        self._client = boto3.client("secretsmanager", region_name=region)

    def get(self, name: str, default: Optional[str] = None) -> Optional[str]:
        try:
            resp = self._client.get_secret_value(SecretId=name)
            return resp.get("SecretString", default)
        except Exception:
            return default


class VaultProvider(SecretProvider):  # pragma: no cover - needs hvac + a Vault server
    """Reads secrets from HashiCorp Vault (KV v2)."""

    def __init__(self, url: str, token: str, mount: str = "secret") -> None:
        import hvac

        self._client = hvac.Client(url=url, token=token)
        self._mount = mount

    def get(self, name: str, default: Optional[str] = None) -> Optional[str]:
        try:
            data = self._client.secrets.kv.v2.read_secret_version(
                path=name, mount_point=self._mount
            )
            return data["data"]["data"].get("value", default)
        except Exception:
            return default


def select_provider() -> SecretProvider:
    """Choose a secret provider from SECRET_PROVIDER (env | aws | vault)."""
    kind = os.getenv("SECRET_PROVIDER", "env").lower()
    if kind == "aws":  # pragma: no cover - requires boto3 + AWS
        try:
            return AwsSecretsManagerProvider(os.getenv("AWS_REGION"))
        except Exception as exc:
            logger.warning("AWS secrets provider unavailable (%s); using env", exc)
    elif kind == "vault":  # pragma: no cover - requires hvac + Vault
        try:
            return VaultProvider(os.getenv("VAULT_ADDR", ""), os.getenv("VAULT_TOKEN", ""))
        except Exception as exc:
            logger.warning("Vault provider unavailable (%s); using env", exc)
    return EnvSecretProvider()


_provider: SecretProvider = EnvSecretProvider()


def set_provider(provider: SecretProvider) -> None:
    """Swap the active secret provider (e.g. AWS/Vault in production)."""
    global _provider
    _provider = provider


def get_secret(name: str, default: Optional[str] = None) -> Optional[str]:
    """Fetch a secret from the active provider."""
    return _provider.get(name, default)


def require_strong_secrets(*, environment: Optional[str] = None) -> None:
    """Enforce a minimum key length; raise in production, warn otherwise.

    Guards against a weak/default SECRET_KEY reaching production (Req 19).
    """
    env = (environment or getattr(settings, "app_env", "development")).lower()
    weak = len(settings.secret_key or "") < MIN_KEY_LENGTH
    if not weak:
        return
    message = f"SECRET_KEY is shorter than the required {MIN_KEY_LENGTH} characters."
    if env == "production":
        raise WeakSecretError(message)
    logger.warning("%s (allowed outside production only)", message)
