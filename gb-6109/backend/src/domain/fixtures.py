BASE = [
    {"recipe": "BR-051", "fine_bp": 2500, "coarse_bp": 7500},
    {"recipe": "BR-052", "fine_bp": 3000, "coarse_bp": 7000},
    {"recipe": "BR-053", "fine_bp": 2800, "coarse_bp": 7200},
]
REVISION = [
    {"recipe": "BR-051", "fine_bp": 2700, "coarse_bp": 7300},
    {"recipe": "BR-052", "fine_bp": 3200, "coarse_bp": 6800},
]

FIXTURES = [
    {
        "code": "BR-K7-1124",
        "title": "K7 段配方签发重试",
        "description": "先在影子页刷盘后断电，再重试发布；比对封存根、孤儿页和释放后的页号复用。",
        "payload": {
            "initial": BASE, "keep_baseline": True,
            "events": [
                {"op": "revise", "phase": "flushed", "changes": REVISION},
                {"op": "revise", "phase": "published", "changes": REVISION},
                {"op": "revise", "phase": "published",
                 "changes": [{"recipe": "BR-053", "fine_bp": 3100, "coarse_bp": 6900}]},
                {"op": "release_baseline"},
                {"op": "revise", "phase": "published",
                 "changes": [{"recipe": "BR-053", "fine_bp": 3100, "coarse_bp": 6900}]},
            ],
        },
    },
    {
        "code": "BR-K7-1125",
        "title": "K7 坏页双根隔离与续跑修复",
        "description": "生效根和封存根共用物理页损坏后，两边配方均隔离；页池占满时凭已签同版留档原位修复，中断可续跑。",
        "payload": {
            "initial": BASE, "keep_baseline": True,
            "events": [
                {"op": "sign_archive"},
                {"op": "revise", "phase": "flushed", "changes": REVISION},
                {"op": "revise", "phase": "published", "changes": REVISION},
                {"op": "corrupt_page", "page": 3},
                {"op": "query_root", "recipe": "BR-053"},
                {"op": "repair_page", "page": 3, "ticket_id": "P3-REPAIR", "interrupt_at": "verified"},
                {"op": "resume_repair", "ticket_id": "P3-REPAIR"},
                {"op": "query_root", "root": "sealed"},
            ],
        },
    },
    {
        "code": "BR-K7-1126",
        "title": "K7 坏页修复与换版签发并发",
        "description": "修复检查点后配方换版签发；生效根始终按新版读取，迟到的旧版修复不覆盖新页。",
        "payload": {
            "initial": BASE, "keep_baseline": True,
            "events": [
                {"op": "sign_archive"},
                {"op": "revise", "phase": "published", "changes": REVISION},
                {"op": "corrupt_page", "page": 3},
                {"op": "repair_page", "page": 3, "ticket_id": "CONCURRENT-P3", "interrupt_at": "verified"},
                {"op": "release_baseline"},
                {"op": "revise", "phase": "published",
                 "changes": [{"recipe": "BR-053", "fine_bp": 3100, "coarse_bp": 6900}]},
                {"op": "query_root", "recipe": "BR-053", "root": "active"},
                {"op": "resume_repair", "ticket_id": "CONCURRENT-P3"},
                {"op": "query_root", "recipe": "BR-053", "root": "active"},
            ],
        },
    },
    {
        "code": "BR-K9-1125",
        "title": "K9 坏页缺同版签档维持隔离",
        "description": "虽有签署副本，但配方版本不同且核不出同版副本；坏页继续隔离，其余配方可查。",
        "payload": {
            "initial": BASE, "keep_baseline": True,
            "events": [
                {"op": "sign_copy", "page_id": 3, "revision": 0,
                 "copy": {"recipe": "BR-053", "fine_bp": 2900, "coarse_bp": 7100}},
                {"op": "revise", "phase": "published", "changes": REVISION},
                {"op": "corrupt_page", "page": 3},
                {"op": "repair_page", "page": 3, "ticket_id": "NO-SAME-VERSION"},
                {"op": "query_root", "root": "active"},
                {"op": "query_root", "root": "sealed"},
            ],
        },
    },
    {
        "code": "BR-K9-1124",
        "title": "K9 段刷盘前断电",
        "description": "暂存配方未落盘时不产生孤儿页；后续正式签发与同值重试可对比。",
        "payload": {
            "initial": BASE, "keep_baseline": False,
            "events": [
                {"op": "revise", "phase": "volatile", "changes": REVISION[:1]},
                {"op": "revise", "phase": "published", "changes": REVISION[:1]},
                {"op": "revise", "phase": "published", "changes": REVISION[:1]},
            ],
        },
    },
]
