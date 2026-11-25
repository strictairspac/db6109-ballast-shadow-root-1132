"""Signed archive copies used to verify physical-page repair.

The simulation uses an HMAC signature rather than a real PKI, but the safety
rule is deliberately strict: a repair source is usable only when its signature
verifies and its recipe version matches the referenced root revision.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Any, Iterable

from src.utils.serial import canonical_json


class ArchiveVerificationError(ValueError):
    """Raised when no signed, same-version archive copy can be verified."""


def _material(record: dict[str, Any]) -> bytes:
    body = {key: value for key, value in record.items() if key != "signature"}
    return canonical_json(body)


def sign_record(record: dict[str, Any], secret: str | bytes = "ballast-demo-archive") -> dict[str, Any]:
    secret_bytes = secret if isinstance(secret, bytes) else str(secret).encode("utf-8")
    signed = dict(record)
    signed["signed"] = True
    signed["signature"] = hmac.new(secret_bytes, _material(signed), hashlib.sha256).hexdigest()
    return signed


def verify_record(record: dict[str, Any], secret: str | bytes = "ballast-demo-archive") -> bool:
    if not isinstance(record, dict) or not record.get("signature"):
        return False
    if record.get("signed") is False:
        return False
    provided = str(record["signature"])
    secret_bytes = secret if isinstance(secret, bytes) else str(secret).encode("utf-8")
    expected = hmac.new(secret_bytes, _material(record), hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided, expected)


def recipe_fingerprint(recipe: Any) -> str:
    version = getattr(recipe, "version", None)
    if version:
        return version
    if isinstance(recipe, dict):
        return hashlib.sha256(canonical_json(recipe)).hexdigest()
    return hashlib.sha256(repr(recipe).encode()).hexdigest()


@dataclass
class SignedArchive:
    """In-memory ledger of signed recipe/root copies."""

    secret: str | bytes = "ballast-demo-archive"
    signer: str = "reviewer"
    copies: list[dict[str, Any]] = field(default_factory=list)

    def sign_recipe(
        self,
        recipe: Any,
        *,
        revision: int | None = None,
        root: str | None = None,
        page_id: int | None = None,
        code: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        recipe_code = code or getattr(recipe, "code", None) or recipe.get("recipe")
        recipe_data = recipe.to_dict() if hasattr(recipe, "to_dict") else dict(recipe)
        record = {
            "code": recipe_code,
            "recipe": recipe_code,
            "version": recipe_fingerprint(recipe),
            "revision": revision,
            "root": root,
            "page_id": page_id,
            "recipe_data": recipe_data,
            "signer": self.signer,
        }
        if extra:
            record.update(extra)
        signed = sign_record(record, self.secret)
        self.copies.append(signed)
        return signed

    def sign_root(self, root_revision: Any, pages: dict[int, Any], root: str = "active") -> list[dict[str, Any]]:
        revision_number = getattr(root_revision, "number", None)
        signed: list[dict[str, Any]] = []
        for code, page_id in sorted(root_revision.pointers.items()):
            signed.append(self.sign_recipe(
                pages[page_id], revision=revision_number, root=root, page_id=page_id, code=code,
            ))
        return signed

    def add(self, record: dict[str, Any]) -> dict[str, Any]:
        signed = sign_record(record, self.secret)
        self.copies.append(signed)
        return signed

    def verified(
        self,
        record: dict[str, Any],
        *,
        code: str | None = None,
        version: str | None = None,
        revision: int | None = None,
        page_id: int | None = None,
    ) -> dict[str, Any] | None:
        if not verify_record(record, self.secret):
            return None
        record_code = record.get("code") or record.get("recipe")
        if code is not None and record_code != code:
            return None
        if version is not None and record.get("version") != version:
            return None
        if revision is not None and record.get("revision") != revision:
            return None
        if page_id is not None and record.get("page_id") != page_id:
            return None
        return record

    def find_verified(
        self,
        *,
        code: str | None = None,
        version: str | None = None,
        revision: int | None = None,
        page_id: int | None = None,
        any_revision: bool = True,
    ) -> dict[str, Any] | None:
        """Return the newest matching verified copy, or ``None``.

        When ``revision`` is supplied, first require that exact root revision.
        A physical page can also be recovered from the same content version in
        another signed root revision, so callers may set ``any_revision`` to
        accept that proof.  A different content version never matches.
        """
        exact: list[dict[str, Any]] = []
        same_version: list[dict[str, Any]] = []
        for record in self.copies:
            checked = self.verified(record, code=code, version=version, page_id=page_id)
            if checked is None:
                continue
            if revision is not None and checked.get("revision") == revision:
                exact.append(checked)
            elif version is not None and checked.get("version") == version and any_revision:
                same_version.append(checked)
        candidates = exact or same_version
        return candidates[-1] if candidates else None

    def require_verified(self, **filters: Any) -> dict[str, Any]:
        copy = self.find_verified(**filters)
        if copy is None:
            raise ArchiveVerificationError("核不出同版签名留档副本")
        return copy

    def recipes(self) -> Iterable[Any]:
        for record in self.copies:
            data = record.get("recipe_data")
            if data is not None:
                yield data


SignedLedger = SignedArchive
RecipeArchive = SignedArchive
Archive = SignedArchive
