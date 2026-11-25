from dataclasses import dataclass
from threading import RLock
from types import MappingProxyType
from typing import Any, Mapping

from .archive import ArchiveVerificationError as ArchiveVerificationError
from .archive import SignedArchive as SignedArchive
from .recipe import Recipe


@dataclass(frozen=True)
class Revision:
    number: int
    pointers: Mapping[str, int]


@dataclass(frozen=True)
class CorruptPage:
    """Explicit unreadable physical-page sentinel."""

    page_id: int
    reason: str = "checksum mismatch"
    corrupted: bool = True


class PageBudgetExceeded(ValueError):
    pass


class QuarantinedPageError(ValueError):
    """Raised when a recipe is read through a quarantined bad page."""

    def __init__(self, code: str, page_id: int, root: str = "active", reason: str = "物理页损坏，配方已隔离"):
        super().__init__(f"{reason}: root={root}, recipe={code}, page={page_id}")
        self.code = code
        self.page_id = page_id
        self.root = root


# Common English/Chinese root spellings used by acceptance scripts.
_ROOT_ALIASES = {
    "active": "active", "current": "active", "effective": "active",
    "published": "active", "生效": "active", "生效根": "active", "当前根": "active",
    "sealed": "sealed", "backup": "sealed", "baseline": "sealed", "pinned": "sealed",
    "archive": "sealed", "封存": "sealed", "封存根": "sealed", "基准根": "sealed",
}

_ROOT_LABELS = {"active": "生效根", "sealed": "封存根"}


class ShadowPager:
    """Keep active pages immutable until the new root is durably published."""

    def __init__(self, recipes, budget, pinned, archive: Any | None = None):
        self.budget = budget
        pointers = {recipe.code: page_id for page_id, recipe in enumerate(recipes, 1)}
        self.pages: dict[int, Any] = {pointers[recipe.code]: recipe for recipe in recipes}
        self.root = Revision(0, MappingProxyType(pointers))
        self.backup = self.root if pinned else None
        self.used = set(self.pages)
        self.archive = archive

        # Fault/repair simulation state.  Sets are the durable in-memory page
        # state; tickets provide resumable checkpoints.
        self._lock = RLock()
        self.page_generation = {page_id: 1 for page_id in self.pages}
        self.revision_versions = {
            0: {recipe.code: recipe.version for recipe in recipes}
        }
        self.corrupt_pages: set[int] = set()
        self.quarantined_pages: set[int] = set()
        self.recovered_pages: set[int] = set()
        self.page_status: dict[int, str] = {}
        self.quarantine_events: list[dict[str, Any]] = []
        self.repair_history: list[dict[str, Any]] = []
        self.repair_tickets: dict[str, Any] = {}

    @property
    def protected(self):
        referenced = set(self.root.pointers.values())
        if self.backup:
            referenced.update(self.backup.pointers.values())
        return referenced

    def reclaim(self):
        with self._lock:
            # A known-bad physical page stays allocated as a quarantine slot;
            # it must never be handed to a new recipe before verified repair.
            returned = sorted(
                page_id for page_id in self.pages.keys() - self.protected
                if page_id not in self.corrupt_pages
            )
            for page_id in returned:
                del self.pages[page_id]
            return returned

    def release(self):
        with self._lock:
            self.backup = None
            return self.reclaim()

    # ------------------------------------------------------------------
    # Root/page inspection and corruption
    # ------------------------------------------------------------------
    def canonical_root_name(self, root: str | None = "active") -> str:
        if root is None:
            return "active"
        return _ROOT_ALIASES.get(str(root).strip(), str(root).strip())

    def get_root_revision(self, root: str | None = "active") -> Revision | None:
        name = self.canonical_root_name(root)
        if name == "active":
            return self.root
        if name == "sealed":
            return self.backup
        raise KeyError(f"未知配方根: {root}")

    # Convenience aliases for callers expecting a method rather than attr.
    def root_revision(self, root: str | None = "active") -> Revision | None:
        return self.get_root_revision(root)

    def root_view(self, root: str | None = "active") -> Revision | None:
        return self.get_root_revision(root)

    get_root = root_view
    inspect = root_view

    def _resolve_page_id(self, page_id=None, code=None, recipe=None, root=None):
        if page_id is not None:
            return int(page_id)
        if code is None and recipe is not None:
            code = recipe if isinstance(recipe, str) else getattr(recipe, "code", None)
        if code is None:
            raise ValueError("损坏页需要 page_id 或配方号")
        root_name = self.canonical_root_name(root)
        names = [root_name] if root is not None else ["active", "sealed"]
        for name in names:
            revision = self.get_root_revision(name)
            if revision and code in revision.pointers:
                return int(revision.pointers[code])
        raise KeyError(f"配方 {code} 不在指定根中")

    def corrupt_page(self, page_id=None, *, code=None, recipe=None, root=None,
                     reason: str = "physical page checksum mismatch"):
        """Mark one physical page corrupt and quarantine every reference."""
        with self._lock:
            page_id = self._resolve_page_id(page_id, code=code, recipe=recipe, root=root)
            if not 1 <= page_id <= self.budget:
                raise ValueError("物理页号超出页池")
            if page_id not in self.pages:
                raise KeyError(f"物理页 {page_id} 不在页池中")
            self.pages[page_id] = CorruptPage(page_id, reason)
            self.corrupt_pages.add(page_id)
            self.quarantined_pages.add(page_id)
            self.recovered_pages.discard(page_id)
            self.page_status[page_id] = "quarantined"
            references = self.page_references(page_id)
            event = {
                "op": "corrupt_page", "page_id": page_id, "reason": reason,
                "references": references,
            }
            self.quarantine_events.append(event)
            return event

    # Common verb aliases.
    damage_page = corrupt_page
    damage = corrupt_page
    corrupt = corrupt_page
    simulate_corruption = corrupt_page
    quarantine_page = corrupt_page
    quarantine = corrupt_page
    isolate_page = corrupt_page
    isolate = corrupt_page

    def page_references(self, page_id: int) -> list[dict[str, Any]]:
        references = []
        for name in ("active", "sealed"):
            revision = self.get_root_revision(name)
            if revision is None:
                continue
            for code, referenced_page in sorted(revision.pointers.items()):
                if int(referenced_page) == int(page_id):
                    version_map = self.revision_versions.get(revision.number, {})
                    references.append({
                        "root": name,
                        "root_label": _ROOT_LABELS[name],
                        "revision": revision.number,
                        "recipe": code,
                        "version": version_map.get(code),
                        "page_id": int(page_id),
                    })
        return references

    def is_corrupt(self, page_id: int) -> bool:
        content = self.pages.get(page_id)
        return (
            page_id in self.corrupt_pages
            or isinstance(content, CorruptPage)
            or getattr(content, "corrupted", False) is True
        )

    is_quarantined = is_corrupt

    @property
    def quarantined_page_ids(self) -> set[int]:
        return set(self.corrupt_pages)

    @property
    def quarantined_codes(self) -> set[str]:
        return {entry["recipe"] for entry in self.root_board()["quarantine"]}

    @property
    def isolated_recipes(self) -> set[str]:
        return self.quarantined_codes

    def quarantine_records(self) -> list[dict[str, Any]]:
        return self.root_board()["quarantine"]

    def isolation_records(self) -> list[dict[str, Any]]:
        return self.quarantine_records()

    def _entry_status(self, page_id: int) -> tuple[str, str, bool]:
        content = self.pages.get(page_id)
        if self.is_corrupt(page_id) or not isinstance(content, Recipe):
            return "quarantined", "仍隔离", False
        if page_id in self.recovered_pages:
            return "recovered", "已恢复", True
        return "healthy", "正常", True

    def inspect_root(self, root: str | None = "active") -> dict[str, Any] | None:
        with self._lock:
            name = self.canonical_root_name(root)
            revision = self.get_root_revision(name)
            if revision is None:
                return None
            entries = []
        for code, page_id in sorted(revision.pointers.items()):
            page_id = int(page_id)
            status, status_label, readable = self._entry_status(page_id)
            entry: dict[str, Any] = {
                "root": name,
                "root_label": _ROOT_LABELS[name],
                "revision": revision.number,
                "recipe": code,
                "code": code,
                "page_id": page_id,
                "page": page_id,
                "status": status,
                "state": status,
                "isolation": "isolated" if status == "quarantined" else status,
                "status_label": status_label,
                "label": status_label,
                "status_text": status_label,
                "quarantined": status == "quarantined",
                "isolated": status == "quarantined",
                "recovered": status == "recovered",
                "readable": readable,
            }
            if readable:
                recipe = self.pages[page_id]
                entry["version"] = getattr(recipe, "version", None)
                entry["fine_bp"] = getattr(recipe, "fine", None)
                entry["coarse_bp"] = getattr(recipe, "coarse", None)
            entries.append(entry)
        quarantined = [entry for entry in entries if entry["quarantined"]]
        recovered = [entry for entry in entries if entry["recovered"]]
        return {
            "root": name,
            "root_label": _ROOT_LABELS[name],
            "revision": revision.number,
            "entries": entries,
            "quarantined": quarantined,
            "recovered": recovered,
            "counts": {
                "total": len(entries),
                "quarantined": len(quarantined),
                "recovered": len(recovered),
                "healthy": len(entries) - len(quarantined) - len(recovered),
            },
        }

    def root_board(self) -> dict[str, Any]:
        active = self.inspect_root("active")
        sealed = self.inspect_root("sealed")
        roots = [root for root in (active, sealed) if root is not None]
        entries = [entry for root in roots for entry in root["entries"]]
        return {
            "active": active,
            "sealed": sealed,
            # Chinese alias helps JSON consumers and browser text checks.
            "生效根": active,
            "封存根": sealed,
            "roots": roots,
            "entries": entries,
            "quarantine": [entry for entry in entries if entry["quarantined"]],
            "isolated": [entry for entry in entries if entry["quarantined"]],
            "recovered": [entry for entry in entries if entry["recovered"]],
            "counts": {
                "quarantined": sum(root["counts"]["quarantined"] for root in roots),
                "isolated": sum(root["counts"]["quarantined"] for root in roots),
                "recovered": sum(root["counts"]["recovered"] for root in roots),
                "physical_pages": len(self.pages),
                "budget": self.budget,
            },
        }

    # Read API ----------------------------------------------------------------
    def read(self, code: str, root: str | None = "active"):
        name = self.canonical_root_name(root)
        revision = self.get_root_revision(name)
        if revision is None:
            raise KeyError(f"根 {root} 尚不存在或已解封")
        if code not in revision.pointers:
            raise KeyError(f"配方 {code} 不在{_ROOT_LABELS.get(name, name)}中")
        page_id = int(revision.pointers[code])
        if self.is_corrupt(page_id):
            raise QuarantinedPageError(code, page_id, name)
        return self.pages[page_id]

    read_recipe = read
    get_recipe = read

    def try_read(self, code: str, root: str | None = "active"):
        """Return ``None`` for a missing or quarantined recipe."""
        try:
            return self.read(code, root)
        except (KeyError, QuarantinedPageError):
            return None

    find_recipe = try_read

    def query_recipe(self, code: str, root: str | None = "active") -> dict[str, Any]:
        """Return a readable recipe or its quarantine status without raising."""
        view = self.inspect_root(root)
        if view is None:
            return {"recipe": code, "root": root, "status": "missing", "readable": False}
        entry = next((item for item in view["entries"] if item["recipe"] == code), None)
        if entry is None:
            return {"recipe": code, "root": view["root"], "status": "missing", "readable": False}
        return entry

    # Repair delegation -------------------------------------------------------
    def repair_service(self, archive: Any | None = None):
        from .repair import PageRepairService
        return PageRepairService(self, archive if archive is not None else self.archive)

    def start_repair(self, page_id=None, *, archive=None, interrupt_at: str | None = "verified",
                     ticket_id: str | None = None, **filters):
        service = self.repair_service(archive)
        return service.start(page_id=page_id, interrupt_at=interrupt_at,
                             ticket_id=ticket_id, **filters)

    def resume_repair(self, ticket: Any, *, archive: Any | None = None, interrupt_at: str | None = None):
        service = self.repair_service(archive)
        ticket_id = getattr(ticket, "ticket_id", ticket)
        return service.resume(ticket_id, interrupt_at=interrupt_at)

    def repair_page(self, page_id=None, *, archive=None, ticket: Any | None = None,
                    interrupt_at: str | None = None, **filters):
        service = self.repair_service(archive)
        if ticket is not None:
            return service.resume(getattr(ticket, "ticket_id", ticket), interrupt_at=interrupt_at)
        return service.repair(page_id=page_id, interrupt_at=interrupt_at, **filters)

    restore_page = repair_page
    recover_page = repair_page
    fix_page = repair_page

    def replace(self, recipes, phase):
        with self._lock:
            current = self.root.pointers
            changed = [
                recipe for recipe in recipes
                if recipe.code not in current or self.pages.get(current[recipe.code]) != recipe
            ]
            if not changed:
                return [], [], [], "配方内容未变"
            free = [page_id for page_id in range(1, self.budget + 1) if page_id not in self.pages]
            if len(free) < len(changed):
                raise PageBudgetExceeded(f"需 {len(changed)} 页，只有 {len(free)} 个未引用页")
            pending = dict(zip(free, changed))
            proposed = current | {recipe.code: page_id for page_id, recipe in pending.items()}
            if phase == "volatile":
                return list(pending), [], [], "已断电，未刷盘的影子页丢弃"
            for page_id in pending:
                self.page_generation[page_id] = self.page_generation.get(page_id, 0) + 1
                # A reallocated page starts healthy in this generation.
                self.corrupt_pages.discard(page_id)
                self.quarantined_pages.discard(page_id)
                self.recovered_pages.discard(page_id)
                self.page_status[page_id] = "healthy"
            self.pages.update(pending)
            self.used.update(pending)
            if phase == "flushed":
                return list(pending), sorted(pending.keys() & self.used), self.reclaim(), "已断电，旧根有效，回收孤儿页"
            current_versions = dict(self.revision_versions.get(self.root.number, {}))
            current_versions.update({recipe.code: recipe.version for recipe in changed})
            self.root = Revision(self.root.number + 1, MappingProxyType(proposed))
            self.revision_versions[self.root.number] = current_versions
            return list(pending), sorted(pending.keys() & self.used), self.reclaim(), "原子发布新根"
