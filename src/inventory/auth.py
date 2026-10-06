import os
import re
from dataclasses import dataclass
from typing import Any

import jwt
from fastapi import Header
from jwt import InvalidTokenError

from .exceptions import AuthenticationError, AuthorizationError


ALLOWED_ALGORITHMS = {"RS256", "HS256"}
ALLOWED_ROLES = {"admin", "manager", "operator"}
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    store_id: str
    customer_id: str
    role: str
    scopes: frozenset[str]
    subject: str

    def can(self, scope: str) -> bool:
        return self.role == "admin" or scope in self.scopes


class JwtAuthenticator:
    """Validates JWTs with an explicit, deployment-configured algorithm."""

    def __init__(self) -> None:
        self.algorithm = os.getenv("JWT_ALGORITHM", "")
        if self.algorithm not in ALLOWED_ALGORITHMS:
            raise RuntimeError("JWT_ALGORITHM must be RS256 or HS256")
        if self.algorithm == "HS256":
            self.key = os.getenv("JWT_SECRET", "")
        else:
            self.key = os.getenv("JWT_PUBLIC_KEY", "").replace("\\n", "\n")
        if not self.key:
            raise RuntimeError("JWT verification key is not configured")

    def authenticate(self, token: str) -> AuthenticatedPrincipal:
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != self.algorithm:
                raise AuthenticationError("Unsupported JWT algorithm", "unsupported_algorithm")
            claims: dict[str, Any] = jwt.decode(
                token,
                self.key,
                algorithms=[self.algorithm],
                options={
                    "require": [
                        "sub",
                        "exp",
                        "iat",
                        "nbf",
                        "store_id",
                        "customer_id",
                        "role",
                        "scope",
                    ]
                },
            )
        except AuthenticationError:
            raise
        except InvalidTokenError as error:
            code = "invalid_token"
            message = str(error)
            for claim in ("exp", "iat", "nbf"):
                if claim in message.lower():
                    code = f"invalid_{claim}"
                    break
            raise AuthenticationError("Invalid authentication token", code) from error

        store_id = claims.get("store_id")
        customer_id = claims.get("customer_id")
        role = claims.get("role")
        scope = claims.get("scope")
        subject = claims.get("sub")
        if not isinstance(store_id, str) or not IDENTIFIER_PATTERN.fullmatch(store_id):
            raise AuthenticationError("Invalid store_id claim", "invalid_store_id")
        if not isinstance(customer_id, str) or not IDENTIFIER_PATTERN.fullmatch(customer_id):
            raise AuthenticationError("Invalid customer_id claim", "invalid_customer_id")
        if role not in ALLOWED_ROLES:
            raise AuthenticationError("Invalid role claim", "invalid_role")
        scopes = self._scopes(scope)
        if not scopes:
            raise AuthenticationError("Invalid scope claim", "invalid_scope")
        if not isinstance(subject, str) or not subject:
            raise AuthenticationError("Invalid subject claim", "invalid_subject")
        return AuthenticatedPrincipal(
            store_id=store_id,
            customer_id=customer_id,
            role=role,
            scopes=frozenset(scopes),
            subject=subject,
        )

    @staticmethod
    def _scopes(value: Any) -> set[str]:
        if isinstance(value, str):
            scopes = set(value.split())
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            scopes = set(value)
        else:
            raise AuthenticationError("Invalid scope claim", "invalid_scope")
        return scopes


def get_principal(authorization: str | None = Header(default=None)) -> AuthenticatedPrincipal:
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthenticationError("Bearer token is required", "missing_token")
    return JwtAuthenticator().authenticate(authorization[7:])


def require_store(principal: AuthenticatedPrincipal, store_id: str) -> None:
    if principal.store_id != store_id:
        raise AuthorizationError("Store access is forbidden")


def require_scope(principal: AuthenticatedPrincipal, scope: str) -> None:
    if not principal.can(scope):
        raise AuthorizationError("Required permission is missing")


def require_admin(principal: AuthenticatedPrincipal) -> None:
    if principal.role != "admin":
        raise AuthorizationError("Administrator permission is required")