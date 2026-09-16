"""E2E 验证：知识库去重合并——提交合并审批后卡片不再被扫描统计（可撤回恢复）。

流程：健康检查 → demo 登录 → 读取审批模式 → 读扫描与在途提案 →
（非 OFF 模式且有分组时）提交合并提案 → 复扫断言排除 → 撤回 → 复扫断言恢复。
撤回保证现场可恢复，仅新增一条 withdrawn 提案与审计记录。
"""

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"


def call(method, path, token=None, body=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


failures = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (" | " + str(detail) if detail else ""))
    if not ok:
        failures.append(name)


# 0. 健康检查
st, health = call("GET", "/health")
check("backend health", st == 200, health)

# 1. 演示登录
st, login = call("POST", "/api/v1/auth/demo-login", body={"email": "demo-admin@atoms.dev"})
token = (login or {}).get("token")
check("demo-login", st == 200 and bool(token), st)

# 2. 审批模式（OFF 会自动合并归档案例，此时仅做只读验证避免破坏数据）
st, cfg = call("GET", "/api/v1/console/configs", token)
mode = next(
    (i.get("value") for i in (cfg or {}).get("items", []) if i.get("key") == "approval_mode"),
    "SINGLE_REVIEW",
)
print("approval_mode =", mode)

# 3. 读取扫描结果与在途提案
st, scan = call("GET", "/api/v1/console/kb/duplicates", token)
groups = (scan or {}).get("groups", [])
st, props = call("GET", "/api/v1/console/kb/merge-proposals", token)
items = (props or {}).get("items", [])
pending = [p for p in items if p.get("status") == "pending"]
covered = set()
for p in pending:
    covered.add(p["master_case_id"])
    covered.update(p.get("merged_case_ids") or [])
print("scan groups:", [g["case_ids"] for g in groups])
print("pending proposals:", [(p["id"], p["master_case_id"], p["merged_case_ids"]) for p in pending])

# 4. 在途提案覆盖的案例不得出现在扫描结果中（新口径是否线上生效）
check(
    "pending-covered cases excluded from scan",
    all(not (covered & set(g["case_ids"])) for g in groups),
    sorted(covered) or "no pending proposals",
)

if mode != "OFF" and groups and token:
    g = groups[0]
    master = g["suggested_master"]
    merged = [c for c in g["case_ids"] if c != master]
    st, created = call(
        "POST", "/api/v1/console/kb/merge-proposals", token,
        {"master_case_id": master, "merged_case_ids": merged, "reason": "E2E 验证：提交后应从扫描中排除"},
    )
    proposal = (created or {}).get("proposal") or {}
    req_id = (created or {}).get("approval_request_id")
    check(
        "create merge proposal",
        st == 200 and proposal.get("status") == "pending" and bool(req_id),
        (st, proposal.get("status"), req_id),
    )

    st, scan2 = call("GET", "/api/v1/console/kb/duplicates", token)
    ids2 = [gg["case_ids"] for gg in (scan2 or {}).get("groups", [])]
    check(
        "submitted group excluded from rescan",
        all(not (set(g["case_ids"]) & set(c)) for c in ids2),
        ids2,
    )

    st, wd = call(
        "POST", f"/api/v1/console/approvals/{req_id}/decide", token,
        {"action": "withdraw", "comment": "E2E 验证完成，撤回恢复现场"},
    )
    check("withdraw approval", st == 200 and (wd or {}).get("status") == "withdrawn", (st, wd))

    st, scan3 = call("GET", "/api/v1/console/kb/duplicates", token)
    ids3 = [gg["case_ids"] for gg in (scan3 or {}).get("groups", [])]
    check(
        "withdrawn group restored in scan",
        set(g["case_ids"]) <= set(sum(ids3, [])),
        ids3,
    )
else:
    print("SKIP submit-cycle（无分组 / OFF 模式 / 无 token）——pending 排除语义已由单元测试覆盖")

print("RESULT:", "ALL_PASS" if not failures else f"FAILED: {failures}")
sys.exit(0 if not failures else 1)
