"""Resumable, concurrency-safe repair for corrupt shadow-pager pages."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4

from .archive import ArchiveVerificationError, SignedArchive, verify_record
from .pager import QuarantinedPageError
from .recipe import Recipe


class RepairInterrupted(RuntimeError):
    """Raised by callers that model a real crash instead of a checkpoint state."""

    def __init__(self, ticket):
        self.ticket = ticket
        super().__init__(f"repair interrupted at {ticket.checkpoint}: {ticket.ticket_id}")


@dataclass
class RepairTicket:
    ticket_id: str
    page_id: int
    generation: int
    state: str = "pending"
    checkpoint: str = "created"
    interrupt_at: str | None = None
    code: str | None = None
    references: list[dict[str, Any]] = field(default_factory=list)
    candidate: dict[str, Any] | None = None
    reason: str | None = None
    attempts: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)

    def mark(self, state: str, checkpoint: str, reason: str | None = None):
        self.state = state
        self.checkpoint = checkpoint
        if reason is not None:
            self.reason = reason
        self.history.append({"state": state, "checkpoint": checkpoint, "reason": reason})
        return self

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        allowed = {key for key in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in dict(data).items() if key in allowed})


class PageRepairService:
    """Verify signed archive copies and restore bad pages without allocation."""

    def __init__(self, pager, archive: Any | None = None, *, secret: str = "ballast-demo-archive"):
        self.pager = pager
        if archive is None:
            archive = getattr(pager, "archive", None)
        if archive is None:
            archive = SignedArchive(secret=secret)
        elif isinstance(archive, list):
            archive = SignedArchive(secret=secret, copies=archive)
        self.archive = archive
        if not hasattr(self.archive, "find_verified"):
            # Support a raw iterable of signed records by wrapping it.
            self.archive = SignedArchive(secret=secret, copies=list(archive))

    def _find_candidate(self, ticket: RepairTicket, filters: dict[str, Any]):
        code = filters.get("code") or ticket.code
        version = filters.get("version")
        revision = filters.get("revision")
        page_id = ticket.page_id

        # Explicit caller proof takes precedence.
        if filters.get("copy") is not None:
            copy = filters["copy"]
            if hasattr(self.archive, "verified"):
                checked = self.archive.verified(copy, code=code, version=version, page_id=page_id)
                if checked is not None:
                    return checked
            elif verify_record(copy):
                return copy
            return None

        # A signed root manifest is the strongest same-version proof.  Try the
        # exact root revision that currently references this physical page.
        for reference in ticket.references:
            candidate = self.archive.find_verified(
                code=reference["recipe"],
                version=reference.get("version"),
                revision=reference["revision"],
                page_id=page_id,
            )
            if candidate is not None:
                if version is None or candidate.get("version") == version:
                    ticket.code = reference["recipe"]
                    return candidate

        # Fall back to a signed content copy.  find_verified enforces version
        # when one is supplied and always enforces the signature.
        return self.archive.find_verified(
            code=code, version=version, revision=revision, page_id=page_id,
        )

    def start(self, page_id=None, *, code=None, copy=None, version=None, revision=None,
              interrupt_at: str | bool | None = "verified", ticket_id: str | None = None,
              crash_at: str | None = None, raise_on_interrupt: bool = False, **_extra):
        if interrupt_at is True:
            interrupt_at = "verified"
        if crash_at is not None:
            interrupt_at = crash_at
        with self.pager._lock:
            if page_id is None:
                page_id = self.pager._resolve_page_id(code=code, **{
                    key: _extra[key] for key in ("recipe", "root") if key in _extra
                })
            page_id = int(page_id)
            ticket_id = ticket_id or f"repair-p{page_id}-{uuid4().hex[:8]}"
            existing = self.pager.repair_tickets.get(ticket_id)
            if existing is not None and existing.state == "interrupted":
                return self.resume(ticket_id, interrupt_at=interrupt_at)

            ticket = RepairTicket(
                ticket_id=ticket_id,
                page_id=page_id,
                generation=int(self.pager.page_generation.get(page_id, 1)),
                interrupt_at=interrupt_at if isinstance(interrupt_at, str) else None,
                code=code,
                references=self.pager.page_references(page_id),
            )
            self.pager.repair_tickets[ticket_id] = ticket
            ticket = self._run(ticket, copy=copy, version=version, revision=revision)
            if raise_on_interrupt and ticket.state == "interrupted":
                raise RepairInterrupted(ticket)
            return ticket

    def repair(self, **kwargs):
        kwargs.setdefault("interrupt_at", None)
        return self.start(**kwargs)

    def resume(self, ticket_or_id: Any, *, interrupt_at: str | bool | None = None,
               raise_on_interrupt: bool = False, **_kwargs):
        if isinstance(ticket_or_id, RepairTicket):
            ticket_id = ticket_or_id.ticket_id
        elif isinstance(ticket_or_id, dict):
            ticket_id = ticket_or_id["ticket_id"]
            self.pager.repair_tickets.setdefault(ticket_id, RepairTicket.from_dict(ticket_or_id))
        else:
            ticket_id = getattr(ticket_or_id, "ticket_id", ticket_or_id)
        with self.pager._lock:
            ticket = self.pager.repair_tickets.get(ticket_id)
            if ticket is None:
                raise KeyError(f"修复检查点不存在: {ticket_id}")
            if ticket.state in {"completed", "superseded"} and interrupt_at is None:
                return ticket
            if interrupt_at is True:
                interrupt_at = "verified"
            # A resume call without a new stop point runs through completion,
            # clearing the previous one-shot interruption checkpoint.
            ticket.interrupt_at = interrupt_at if isinstance(interrupt_at, str) else None
            ticket.attempts += 1
            ticket = self._run(ticket)
            if raise_on_interrupt and ticket.state == "interrupted":
                raise RepairInterrupted(ticket)
            return ticket

    def _should_interrupt(self, ticket: RepairTicket, phase: str) -> bool:
        stop = ticket.interrupt_at
        if phase == "verified":
            return stop in {"verified", "before_write", "before_restore"}
        if phase == "restored":
            return stop in {"restored", "written", "after_write"}
        return False

    def _run(self, ticket: RepairTicket, **filters) -> RepairTicket:
        pager = self.pager
        pid = ticket.page_id
        ticket.attempts += 1 if ticket.checkpoint == "created" else 0

        # A completed ticket is idempotent.
        if ticket.state == "completed":
            return ticket

        current_generation = int(pager.page_generation.get(pid, ticket.generation))
        if current_generation != ticket.generation or pid not in pager.pages:
            ticket.mark("superseded", "version_changed",
                        "物理页已换版或重新分配，旧修复不得覆盖新版")
            pager.repair_history.append(ticket.to_dict())
            return ticket

        if ticket.checkpoint in {"created", "verifying", "verification_failed"}:
            ticket.mark("verifying", "verifying")
            candidate = self._find_candidate(ticket, filters)
            if candidate is None:
                ticket.mark("quarantined", "verification_failed", "核不出同版签名留档副本，维持隔离")
                pager.page_status[pid] = "quarantined"
                pager.repair_history.append(ticket.to_dict())
                return ticket
            ticket.candidate = candidate
            ticket.code = candidate.get("code") or candidate.get("recipe") or ticket.code
            ticket.mark("verified", "verified", "同版留档签名核验通过")

        if ticket.state == "interrupted" and ticket.checkpoint == "verified":
            # Resume from the durable verification checkpoint.
            pass

        if ticket.checkpoint == "verified":
            if self._should_interrupt(ticket, "verified"):
                ticket.state = "interrupted"
                ticket.reason = "修复已核验，写入前中断；可凭检查点续跑"
                ticket.history.append({"state": "interrupted", "checkpoint": "verified",
                                       "reason": ticket.reason})
                return ticket

        # Physical restore phase.  This writes the same page id in place, so a
        # completely full page pool does not block every verifiable repair.
        if ticket.checkpoint in {"verified", "restored"}:
            if ticket.checkpoint != "restored":
                if not pager.is_corrupt(pid) and isinstance(pager.pages.get(pid), Recipe):
                    # Another path already restored the page.
                    pass
                else:
                    data = (ticket.candidate or {}).get("recipe_data")
                    if data is None:
                        ticket.mark("quarantined", "restore_failed", "留档缺少配方内容")
                        return ticket
                    try:
                        recipe = Recipe(
                            code=data["code"], fine=int(data["fine"]), coarse=int(data["coarse"]),
                        )
                    except (KeyError, TypeError, ValueError) as exc:
                        ticket.mark("quarantined", "restore_failed", f"留档配方无法读取: {exc}")
                        return ticket
                    if recipe.version != ticket.candidate.get("version"):
                        ticket.mark("quarantined", "version_mismatch", "留档版本与签名摘要不一致")
                        return ticket
                    pager.pages[pid] = recipe
                pager.corrupt_pages.discard(pid)
                pager.quarantined_pages.discard(pid)
                pager.recovered_pages.add(pid)
                pager.page_status[pid] = "recovered"
                ticket.mark("restored", "restored", "已用同版签名留档原位恢复")

            if self._should_interrupt(ticket, "restored") and ticket.state != "interrupted":
                ticket.state = "interrupted"
                ticket.reason = "页已写回，完成检查点前中断；续跑幂等"
                ticket.history.append({"state": "interrupted", "checkpoint": "restored",
                                       "reason": ticket.reason})
                return ticket

        ticket.mark("completed", "completed", "坏页修复完成")
        # A restored page no longer needed by any root can now be recycled.
        try:
            pager.reclaim()
        except Exception:
            pass
        pager.repair_history.append(ticket.to_dict())
        return ticket


# Common naming aliases for unit/integration callers.
RepairService = PageRepairService
RepairManager = PageRepairService
PageRepairManager = PageRepairService


def corrupt_page(pager, *args, **kwargs):
    return pager.corrupt_page(*args, **kwargs)


def repair_page(pager, *args, **kwargs):
    return PageRepairService(pager, kwargs.pop("archive", None)).repair(*args, **kwargs)


def resume_repair(pager, ticket, **kwargs):
    return PageRepairService(pager, kwargs.pop("archive", None)).resume(ticket, **kwargs)


__all__ = [
    "ArchiveVerificationError", "PageRepairService", "RepairInterrupted", "RepairManager",
    "RepairService", "PageRepairManager", "RepairTicket", "QuarantinedPageError",
    "corrupt_page", "repair_page", "resume_repair",
]
