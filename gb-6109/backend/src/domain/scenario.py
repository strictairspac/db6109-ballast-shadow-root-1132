from .archive import SignedArchive
from .fixtures import FIXTURES  # noqa: F401  # re-exported for seed/API callers
from .pager import PageBudgetExceeded, ShadowPager
from .recipe import recipe_set


CONFIG = {
    "title": "岑砾道料·道砟配方影子页签发",
    "team": "岑砾道料 8 人试配与线下签发小组",
    "description": "用影子分页提交配方，对照断电前后有效根和可复用页，再安排生产签发。",
    "direction": "影子分页与写时复制发布",
    "mechanism": "不可变配方页、原子根指针切换、引用保护及孤儿页回收。",
    "modules": ["道料交班", "配方签发单", "发布重演", "可见配方根"],
    "unit": "配方签发单",
    "max_parameter": 128,
    "parameter_label": "可用物理页数（1–128）",
    "default_parameter": 5,
    "facts": ["已发布根次", "回收页次", "预算拒绝"],
    "palette": 9,
    "fixture": "BR-K7-1124 / K7 段",
}

PHASES = {"volatile": "刷盘前", "flushed": "刷盘后换根前", "published": "换根成功"}

_CORRUPT_OPS = {
    "corrupt", "corrupt_page", "damage", "damage_page", "quarantine",
    "quarantine_page", "simulate_corruption", "page_corruption",
    "physical_page_corruption", "page_corrupted", "bad_page", "page_fault",
}
_ARCHIVE_OPS = {"sign_archive", "archive", "seal_archive", "sign", "sign_off", "signed_archive"}
_ARCHIVE_COPY_OPS = {"sign_copy", "archive_copy", "sign_archive_copy"}
_REPAIR_OPS = {
    "repair", "repair_page", "restore", "restore_page", "recover", "recover_page", "fix_page",
    "repair_corrupt_page", "restore_from_archive", "verify_and_repair",
}
_RESUME_OPS = {"resume_repair", "continue_repair", "resume", "retry_repair", "repair_resume"}
_QUERY_OPS = {"query_root", "inspect_root", "root_status", "query", "lookup"}
_REVISE_OPS = {"revise", "publish", "issue", "revise_recipe", "publish_recipe"}


def _event_target(event):
    page_id = event.get("page_id", event.get("page", event.get("physical_page")))
    code = event.get("code", event.get("recipe"))
    root = event.get("root", event.get("root_name"))
    return page_id, code, root


def normalize(payload):
    if not isinstance(payload, dict):
        raise ValueError("输入需为对象")
    initial = recipe_set(payload.get("initial"), "initial")
    keep = payload.get("keep_baseline")
    if type(keep) is not bool:
        raise ValueError("keep_baseline 须为布尔值")
    events = payload.get("events")
    if not isinstance(events, list) or not 1 <= len(events) <= 64:
        raise ValueError("events 须有 1 至 64 个操作")
    normalized = []
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("events 各项必须是对象")
        op = event.get("op") or event.get("action") or event.get("type")
        if op == "release_baseline" or op in {"release", "unpin_baseline"}:
            normalized.append(("release_baseline", []))
            continue
        if op in _CORRUPT_OPS:
            page_id, code, root = _event_target(event)
            if page_id is None and not code:
                raise ValueError("损坏页操作需提供 page/page_id 或 recipe/code")
            normalized.append(("corrupt", {
                "page_id": int(page_id) if page_id is not None else None,
                "code": code, "root": root,
                "reason": event.get("reason", "物理页校验失败"),
            }))
            continue
        if op in _ARCHIVE_OPS:
            roots = event.get("roots", event.get("root", ["active", "sealed"]))
            if isinstance(roots, str):
                roots = [roots]
            normalized.append(("archive", {"roots": roots}))
            continue
        if op in _ARCHIVE_COPY_OPS:
            source = event.get("copy") or event.get("record") or event.get("recipe") or event
            recipes = recipe_set([source], "archive_copy")
            normalized.append(("archive_copy", {
                "recipe": recipes[0],
                "revision": event.get("revision"),
                "page_id": event.get("page_id", event.get("page")),
                "root": event.get("root"),
            }))
            continue
        if op in _REPAIR_OPS or op in _RESUME_OPS:
            page_id, code, root = _event_target(event)
            interrupt = event.get("interrupt_at", event.get("crash_at", event.get("stop_at")))
            if interrupt is None and event.get("interrupt") is True:
                interrupt = "verified"
            normalized.append(("resume_repair" if op in _RESUME_OPS else "repair", {
                "page_id": int(page_id) if page_id is not None else None,
                "code": code,
                "root": root,
                "ticket_id": event.get("ticket_id", event.get("ticket", event.get("id"))),
                "resume": bool(event.get("resume", op in _RESUME_OPS)),
                "interrupt_at": interrupt,
                "copy": event.get("copy", event.get("archive_copy")),
            }))
            continue
        if op in _QUERY_OPS:
            _, code, root = _event_target(event)
            normalized.append(("query", {"code": code, "root": root or "active"}))
            continue

        phase = event.get("phase")
        if op not in _REVISE_OPS:
            raise ValueError(
                "op 需为 revise、release_baseline、corrupt_page、sign_archive、"
                "sign_copy、repair_page 或 resume_repair"
            )
        if phase is None and op in {"publish", "issue", "publish_recipe"}:
            phase = "published"
        if not isinstance(phase, str) or phase not in PHASES:
            raise ValueError("revise 需提供 volatile、flushed 或 published 阶段")
        normalized.append((phase, recipe_set(event.get("changes"), "changes")))
    return initial, keep, normalized


def validate(payload):
    normalize(payload)


def page_list(page_ids):
    return "、".join(str(page_id) for page_id in page_ids) or "—"


def _ticket_label(ticket):
    state = getattr(ticket, "state", None)
    page_id = getattr(ticket, "page_id", "?")
    if state == "completed":
        return f"物理页 {page_id} 已用核签通过的同版留档修复，受影响根配方已恢复。"
    if state == "interrupted":
        if ticket.checkpoint == "verified":
            return f"物理页 {page_id} 的同版留档已核验；修复在写回前中断，当前仍隔离，可凭检查点续跑。"
        return f"物理页 {page_id} 已写回但完成检查点中断；续跑后正式标记恢复。"
    if state == "quarantined":
        return f"物理页 {page_id} 核不出同版签名副本，维持隔离。"
    if state == "superseded":
        return f"物理页 {page_id} 已换版，旧修复停止覆盖；当前生效根按新版读取。"
    return f"物理页 {page_id} 修复状态：{state}。"


def run(payload, parameter, db=None):
    initial, keep, events = normalize(payload)
    if parameter < len(initial):
        raise ValueError("物理页预算放不下初始配方根")

    archive_records = payload.get("archive", payload.get("signed_archive", []))
    if archive_records is None:
        archive_records = []
    if not isinstance(archive_records, list):
        raise ValueError("archive 须为签名留档副本数组")
    archive = SignedArchive(secret=payload.get("archive_secret", "ballast-demo-archive"),
                            copies=archive_records)
    pager = ShadowPager(initial, parameter, keep, archive=archive)

    denied = recovered = reused = 0
    repaired = repair_denied = interrupted = 0
    completed_ticket_ids: set[str] = set()
    denied_ticket_ids: set[str] = set()
    interrupted_ticket_ids: set[str] = set()
    feature_used = False
    last_ticket_id = None
    rows = []

    for index, event in enumerate(events, 1):
        first = event[0]

        if first == "release_baseline":
            feature_used = feature_used or False
            released = pager.release()
            label = "释放基准快照"
            rows.append({
                "记录": f"步骤 {index}", "阶段": "解封基准",
                "当前根": pager.root.number, "影子页": "—",
                "回收页": page_list(released), "复用页": "—", "结论": label,
            })
            recovered += len(released)
            continue

        if first in PHASES:
            phase, changes = event
            assigned = recycled = released = []
            try:
                assigned, recycled, released, label = pager.replace(changes, phase)
            except PageBudgetExceeded as exc:
                denied += 1
                label = str(exc)
            recovered += len(released)
            reused += len(recycled)
            rows.append({
                "记录": f"步骤 {index}", "阶段": PHASES.get(phase, phase),
                "当前根": pager.root.number, "影子页": page_list(assigned),
                "回收页": page_list(released), "复用页": page_list(recycled),
                "结论": label,
            })
            continue

        op, params = event
        feature_used = True

        if op == "archive":
            signed_count = 0
            for root_name in params["roots"]:
                revision = pager.get_root_revision(root_name)
                if revision is None:
                    continue
                signed_count += len(archive.sign_root(revision, pager.pages,
                                                       pager.canonical_root_name(root_name)))
            rows.append({
                "记录": f"步骤 {index}", "阶段": "已签留档",
                "当前根": pager.root.number, "影子页": "—", "回收页": "—", "复用页": "—",
                "结论": f"已封存并签署 {signed_count} 份当前根配方副本；修复只认可核验的同版副本。",
            })
            continue

        if op == "archive_copy":
            record = archive.sign_recipe(
                params["recipe"], revision=params.get("revision"),
                root=params.get("root"), page_id=params.get("page_id"),
            )
            rows.append({
                "记录": f"留档 {params['recipe'].code}", "阶段": "签署副本",
                "当前根": pager.root.number,
                "影子页": str(params.get("page_id") or "—"),
                "回收页": "—", "复用页": "—",
                "结论": f"已有签署副本，版本摘要 {record['version'][:12]}；非同版不能用于修复。",
            })
            continue

        if op == "corrupt":
            corruption = pager.corrupt_page(**{
                key: value for key, value in params.items() if key in {"page_id", "code", "root", "reason"}
            })
            refs = corruption["references"]
            ref_text = "；".join(f"{item['root_label']} {item['recipe']} 仍隔离" for item in refs) or "当前无根引用"
            rows.append({
                "记录": f"坏页 P{corruption['page_id']}", "阶段": "模拟物理页损坏",
                "当前根": pager.root.number, "影子页": str(corruption["page_id"]),
                "回收页": "—", "复用页": "—", "隔离状态": ref_text,
                "结论": f"物理页 {corruption['page_id']} 损坏并隔离。{ref_text}；其余配方仍可照常查询。",
            })
            continue

        if op == "query":
            code = params.get("code")
            root_name = params.get("root") or "active"
            if code:
                entry = pager.query_recipe(code, root_name)
                conclusion = (
                    f"{entry.get('root_label', root_name)} {code}：{entry['status_label']}"
                    if entry.get("status") != "missing"
                    else f"{root_name} 中查不到配方 {code}"
                )
                if entry.get("readable"):
                    conclusion += f"，细料 {entry['fine_bp']} / 粗料 {entry['coarse_bp']}"
            else:
                view = pager.inspect_root(root_name)
                if view is None:
                    conclusion = f"{root_name} 根不存在或基准已解封"
                else:
                    conclusion = "；".join(f"{item['recipe']} {item['status_label']}" for item in view["entries"])
            rows.append({
                "记录": f"步骤 {index}", "阶段": "可见根查询",
                "当前根": pager.root.number, "影子页": "—", "回收页": "—", "复用页": "—",
                "隔离状态": conclusion, "结论": conclusion,
            })
            continue

        if op in {"repair", "resume_repair"}:
            ticket_id = params.get("ticket_id") or last_ticket_id
            try:
                if op == "resume_repair" or params.get("resume"):
                    if not ticket_id:
                        raise ValueError("续跑缺少修复检查点编号")
                    ticket = pager.resume_repair(ticket_id, interrupt_at=params.get("interrupt_at"))
                else:
                    ticket = pager.start_repair(
                        page_id=params.get("page_id"), code=params.get("code"),
                        root=params.get("root"), copy=params.get("copy"),
                        ticket_id=ticket_id, interrupt_at=params.get("interrupt_at"),
                    )
                    last_ticket_id = ticket.ticket_id
            except Exception as exc:
                repair_denied += 1
                rows.append({
                    "记录": f"步骤 {index}", "阶段": "坏页修复",
                    "当前根": pager.root.number,
                    "影子页": str(params.get("page_id") or "—"),
                    "回收页": "—", "复用页": "—", "隔离状态": "仍隔离",
                    "结论": f"修复未获同版留档证明，维持隔离：{exc}",
                })
                continue

            if ticket.state == "completed" and ticket.ticket_id not in completed_ticket_ids:
                completed_ticket_ids.add(ticket.ticket_id)
                repaired += 1
            elif ticket.state == "quarantined" and ticket.ticket_id not in denied_ticket_ids:
                denied_ticket_ids.add(ticket.ticket_id)
                repair_denied += 1
            elif ticket.state == "interrupted" and ticket.ticket_id not in interrupted_ticket_ids:
                interrupted_ticket_ids.add(ticket.ticket_id)
                interrupted += 1
            rows.append({
                "记录": ticket.ticket_id, "阶段": "坏页修复续跑" if op == "resume_repair" else "坏页修复",
                "当前根": pager.root.number, "影子页": str(ticket.page_id),
                "回收页": "—", "复用页": "—",
                "隔离状态": "已恢复" if ticket.state in {"completed", "restored"} else (
                    "仍隔离" if ticket.state in {"quarantined", "interrupted"} and ticket.checkpoint != "restored" else "已恢复"),
                "结论": _ticket_label(ticket),
            })
            continue

    board = pager.root_board() if feature_used else None

    for code, page_id in sorted(pager.root.pointers.items()):
        entry = pager.query_recipe(code, "active")
        row = {
            "记录": code, "阶段": "生效配方", "当前根": pager.root.number,
            "影子页": str(page_id), "回收页": "—", "复用页": "—",
        }
        if feature_used:
            row["根状态"] = entry["status_label"]
        if entry["readable"]:
            recipe = pager.pages[page_id]
            row["结论"] = f"细料 {recipe.fine} 基点 / 粗料 {recipe.coarse} 基点"
        else:
            row["结论"] = "物理页损坏，配方仍隔离；核不出同版签名留档前不得按猜测值签发。"
        rows.append(row)

    summary = f"生效根 R{pager.root.number}，回收 {recovered} 页次，预算拒绝 {denied} 次。"
    metrics = {"已发布根次": pager.root.number, "回收页次": recovered,
               "预算拒绝": denied, "复用页次": reused, "占用物理页": len(pager.pages)}
    result = {"summary": summary, "metrics": metrics, "rows": rows}

    if feature_used:
        current_quarantine = board["counts"]["quarantined"]
        result["summary"] = (
            summary + f" 当前根表仍隔离 {current_quarantine} 项，"
                      f"修复完成 {repaired} 张坏页，无同版证明维持隔离 {repair_denied} 次。"
        )
        metrics.update({
            "隔离根项": current_quarantine,
            "已恢复根项": board["counts"]["recovered"],
            "损坏物理页": len(pager.corrupt_pages),
            "修复完成": repaired,
            "修复维持隔离": repair_denied,
            "修复中断检查点": interrupted,
        })
        ticket_dicts = [ticket.to_dict() for ticket in pager.repair_tickets.values()]
        result.update({
            "root_board": board,
            "roots": board,
            "root_status": board,
            "visible_roots": board,
            "quarantine_board": board,
            "quarantined_recipes": board["quarantine"],
            "isolated_recipes": board["isolated"],
            "recovered_recipes": board["recovered"],
            "repair_tickets": ticket_dicts,
            "repair_history": pager.repair_history,
            "archive_copies": archive.copies,
        })
    return result
