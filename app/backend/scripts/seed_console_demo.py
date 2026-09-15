"""控制台演示种子数据（对齐设计说明书 §6.3）。

覆盖 11 张控制台表：
- events：已诊断 / 低置信 / LLM 降级 / 未知 / 待处理五类事件，7 天分布
- kb_cases：12 个知识案例（含相似组、归档案例、反馈分数分布）
- kb_versions：历史版本快照
- kb_change_sets + approval_requests + approval_steps：审批闭环演示
- kb_merge_proposals：相似案例合并提案
- rule_versions：3 个规则版本，v3 激活
- unknown_templates：pending / promoted / discarded
- audit_logs：操作审计链路
- console_configs：配置中心键值

重复运行先清空业务表再写入（users / oidc_states 不受影响）。
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, text

from core.database import db_manager
from models.Events import Events
from models.approval_requests import Approval_requests
from models.approval_steps import Approval_steps
from models.audit_logs import Audit_logs
from models.console_configs import Console_configs
from models.kb_cases import Kb_cases
from models.kb_change_sets import Kb_change_sets
from models.kb_merge_proposals import Kb_merge_proposals
from models.kb_versions import Kb_versions
from models.rule_versions import Rule_versions
from models.unknown_templates import Unknown_templates
from services.console_common import CONFIG_DEFAULTS

NOW = datetime.now(timezone.utc)

OPERATOR = "demo-operator@atoms.dev"
SRE = "demo-sre@atoms.dev"
LEAD = "demo-lead@atoms.dev"
ADMIN = "demo-admin@atoms.dev"


def hours_ago(hours: float) -> datetime:
    return NOW - timedelta(hours=hours)


def iso(hours: float) -> str:
    return hours_ago(hours).isoformat()


# ---------------- 知识案例 ----------------

KB_CASES = [
    dict(case_id="KB-001", error_type="gateway_502", service_name="api-gateway", cluster="prod-cluster-1",
         alert_template="HTTP 502 Bad Gateway from upstream <SVC> after <NUM> ms",
         root_cause="payment-service 滚动发布期间就绪探针未通过，api-gateway 上游连接池残留失效端点，转发请求返回 502。",
         solution="1) kubectl rollout status 确认发布进度；2) 触发 api-gateway 上游端点刷新；3) 为 payment-service 增加 startupProbe 宽限期。",
         topology_snapshot="api-gateway -> payment-service", status="active", version=3, feedback_score=2.0),
    dict(case_id="KB-002", error_type="timeout", service_name="payment-service", cluster="prod-cluster-1",
         alert_template="Payment provider call timeout after <NUM> ms order <OID>",
         root_cause="第三方支付通道夜间批处理导致响应 P99 升至 3s 以上，触发 3s 超时熔断。",
         solution="1) 切换备用支付通道；2) 将超时阈值提升至 5s 并配置指数退避重试；3) 与通道方确认批处理窗口。",
         topology_snapshot="payment-service -> payment-provider(外部)", status="active", version=2, feedback_score=1.5),
    dict(case_id="KB-003", error_type="oom_killed", service_name="order-service", cluster="prod-cluster-1",
         alert_template="Container <NAME> OOMKilled exit code <NUM> memory limit <NUM>Mi",
         root_cause="大促期间订单详情页缓存未设上限，堆内存超限被 cgroup 杀死（OOMKilled 137）。",
         solution="1) 重启 Pod 恢复；2) 为本地缓存增加容量上限与 LRU 淘汰；3) 内存 limit 调整为 2Gi 并压测验证。",
         topology_snapshot="order-service -> redis-cache", status="active", version=2, feedback_score=3.0),
    dict(case_id="KB-004", error_type="connection_refused", service_name="inventory-service", cluster="prod-cluster-2",
         alert_template="Connection refused to inventory-db:<NUM> retry <NUM>",
         root_cause="inventory-db 主备切换后旧连接池未释放，应用持续向已降级的旧主节点建连被拒。",
         solution="1) 确认 pg 主备角色；2) 重启 inventory-service 连接池；3) 连接串改用读写分离代理域名。",
         topology_snapshot="inventory-service -> inventory-db", status="active", version=2, feedback_score=0.5),
    dict(case_id="KB-005", error_type="disk_full", service_name="log-collector", cluster="prod-cluster-1",
         alert_template="Disk usage above <NUM> percent on <PATH>",
         root_cause="调试日志级别误开为 DEBUG 且滚动策略失效，/var/log 分区写满。",
         solution="1) 清理 7 天前归档日志；2) 日志级别回滚为 INFO；3) 修复 logrotate 配置并加盘容告警。",
         topology_snapshot="log-collector -> elasticsearch", status="active", version=2, feedback_score=1.0),
    dict(case_id="KB-006", error_type="cpu_throttling", service_name="recommendation-service", cluster="prod-cluster-2",
         alert_template="CPU throttling ratio <NUM> percent for container <NAME>",
         root_cause="推荐模型推理批量过大，CPU limit 2 核下 CFS 节流比例超 30%，推理延迟抬升。",
         solution="1) limit 提升至 4 核；2) 推理 batch size 降为 16；3) 观察节流比例回落至 10% 以下。",
         topology_snapshot="recommendation-service -> feature-store", status="active", version=2, feedback_score=-1.0),
    dict(case_id="KB-007", error_type="timeout", service_name="user-service", cluster="prod-cluster-1",
         alert_template="Redis command timeout GET session:<UID> after <NUM> ms",
         root_cause="Redis 节点持久化 AOF rewrite 期间 fork 阻塞，GET 命令排队超时。",
         solution="1) 将 AOF rewrite 调度至低峰；2) 客户端超时降至 200ms 快速失败；3) 读写分离分担主节点压力。",
         topology_snapshot="user-service -> redis-session", status="active", version=1, feedback_score=0.0),
    dict(case_id="KB-008", error_type="gateway_502", service_name="api-gateway", cluster="prod-cluster-1",
         alert_template="HTTP 502 upstream order-service read timeout after <NUM> ms",
         root_cause="order-service 线程池打满导致上游读超时，api-gateway 返回 502。",
         solution="1) 线程池扩容并开启队列监控；2) 对下游慢接口加舱壁隔离；3) 复盘压测容量水位。",
         topology_snapshot="api-gateway -> order-service", status="active", version=1, feedback_score=0.5),
    dict(case_id="KB-009", error_type="connection_refused", service_name="inventory-service", cluster="prod-cluster-2",
         alert_template="Connection refused to inventory-db replica:<NUM> retry <NUM>",
         root_cause="inventory-db 只读副本重启后未恢复，库存读流量建连被拒。",
         solution="1) 检查副本健康与复制延迟；2) 临时切读主库；3) 修复副本自动拉起巡检。",
         topology_snapshot="inventory-service -> inventory-db", status="active", version=1, feedback_score=0.0),
    dict(case_id="KB-010", error_type="timeout", service_name="notification-service", cluster="prod-cluster-2",
         alert_template="SMTP send timeout to mx.<DOMAIN> after <NUM> ms",
         root_cause="邮件服务商出口 IP 被对端限流，SMTP 握手超时。",
         solution="1) 启用备用 SMTP 通道；2) 队列削峰并退避重试；3) 申请对端白名单。",
         topology_snapshot="notification-service -> smtp-gateway(外部)", status="active", version=1, feedback_score=0.0),
    dict(case_id="KB-011", error_type="gateway_502", service_name="api-gateway", cluster="staging-cluster",
         alert_template="HTTP 502 Bad Gateway from upstream <SVC> after <NUM> ms",
         root_cause="（历史归档）staging 环境上游误配端口，已由 KB-001 覆盖。",
         solution="归档说明：与 KB-001 重复，保留 KB-001 为准。",
         topology_snapshot="api-gateway -> payment-service", status="archived", version=1, feedback_score=0.0),
    dict(case_id="KB-012", error_type="oom_killed", service_name="user-service", cluster="prod-cluster-1",
         alert_template="Container <NAME> OOMKilled exit code <NUM> memory limit <NUM>Mi",
         root_cause="会话序列化对象过大，反序列化峰值内存超限被杀。",
         solution="1) 精简 session 字段；2) 堆外缓存限流；3) limit 提升至 1.5Gi。",
         topology_snapshot="user-service -> redis-session", status="active", version=1, feedback_score=0.0),
]

CASES_BY_ID = {c["case_id"]: c for c in KB_CASES}


def snap(case_id: str, **overrides) -> dict:
    base = {k: v for k, v in CASES_BY_ID[case_id].items() if k not in ("feedback_score",)}
    base.update(overrides)
    return base


# ---------------- 召回候选 ----------------

def cand(case_id: str, score: float) -> dict:
    c = CASES_BY_ID[case_id]
    return {
        "case_id": case_id,
        "error_type": c["error_type"],
        "service_name": c["service_name"],
        "score": score,
        "root_cause": c["root_cause"],
        "solution": c["solution"],
        "feedback_score": c["feedback_score"],
    }


def ai_out(root: str, sol: str, conf: float, cmd: str = "") -> dict:
    return {"root_cause": root, "solution": sol, "confidence": conf, "command": cmd}


# ---------------- 告警事件 ----------------
# (idx, severity, service, cluster, error_type, template, raw_log, fingerprint, topology,
#  hours, status, rag_status, rag_score, candidates, ai, degraded)

G502 = "HTTP 502 Bad Gateway from upstream <SVC> after <NUM> ms"
PAY_TMO = "Payment provider call timeout after <NUM> ms order <OID>"
OOM = "Container <NAME> OOMKilled exit code <NUM> memory limit <NUM>Mi"
CONN = "Connection refused to inventory-db:<NUM> retry <NUM>"
DISK = "Disk usage above <NUM> percent on <PATH>"
CPU = "CPU throttling ratio <NUM> percent for container <NAME>"
RDS_TMO = "Redis command timeout GET session:<UID> after <NUM> ms"
SMTP_TMO = "SMTP send timeout to mx.<DOMAIN> after <NUM> ms"

EVENTS_SPEC = [
    # ---- 已诊断成功（含低置信） ----
    dict(idx=1, sev="critical", svc="api-gateway", cluster="prod-cluster-1", etype="gateway_502",
         tpl=G502, raw="2026-09-11 09:14:21 ERROR gateway upstream 502 payment-service after 1023 ms", fp="fp-9f01a2",
         topo="api-gateway -> payment-service", hours=0.5, status="diagnosed", rag="success", score=0.82,
         cands=[("KB-001", 0.82), ("KB-008", 0.61)],
         ai=ai_out("payment-service 滚动发布中就绪探针未通过，网关上游池残留失效端点。",
                   "确认发布完成后刷新网关上游端点；为服务补充 startupProbe。",
                   0.88, "kubectl -n prod rollout status deploy/payment-service")),
    dict(idx=2, sev="critical", svc="payment-service", cluster="prod-cluster-1", etype="timeout",
         tpl=PAY_TMO, raw="2026-09-11 08:52:03 WARN pay provider timeout 3210 ms order O-88231", fp="fp-7c22b3",
         topo="payment-service -> payment-provider(外部)", hours=1.2, status="diagnosed", rag="success", score=0.79,
         cands=[("KB-002", 0.79), ("KB-010", 0.42)],
         ai=ai_out("第三方支付通道夜间批处理导致响应 P99 超过 3s 熔断阈值。",
                   "切换备用通道并提升超时至 5s，配置指数退避重试。",
                   0.83, "")),
    dict(idx=3, sev="warning", svc="order-service", cluster="prod-cluster-1", etype="oom_killed",
         tpl=OOM, raw="2026-09-11 08:20:44 FATAL order-svc OOMKilled exit 137 limit 1024Mi", fp="fp-3d55c8",
         topo="order-service -> redis-cache", hours=2.0, status="diagnosed", rag="success", score=0.85,
         cands=[("KB-003", 0.85)],
         ai=ai_out("订单详情缓存无上限增长，堆内存超限触发 OOMKilled(137)。",
                   "重启恢复后为缓存加 LRU 上限，limit 提升至 2Gi。",
                   0.91, "kubectl -n prod delete pod -l app=order-service")),
    dict(idx=4, sev="warning", svc="inventory-service", cluster="prod-cluster-2", etype="connection_refused",
         tpl=CONN, raw="2026-09-11 07:05:12 ERROR inv conn refused inventory-db:5432 retry 3", fp="fp-1e88d0",
         topo="inventory-service -> inventory-db", hours=3.5, status="diagnosed", rag="success", score=0.77,
         cands=[("KB-004", 0.77), ("KB-009", 0.70)],
         ai=ai_out("数据库主备切换后旧连接池未释放，持续向旧主建连被拒。",
                   "确认主备角色后重启连接池，连接串切换代理域名。",
                   0.80, "kubectl -n prod rollout restart deploy/inventory-service")),
    dict(idx=5, sev="info", svc="log-collector", cluster="prod-cluster-1", etype="disk_full",
         tpl=DISK, raw="2026-09-11 06:40:09 WARN disk usage 93 percent on /var/log", fp="fp-5a11e7",
         topo="log-collector -> elasticsearch", hours=5.0, status="diagnosed", rag="success", score=0.74,
         cands=[("KB-005", 0.74)],
         ai=ai_out("DEBUG 级别误开且 logrotate 失效，日志分区写满。",
                   "清理归档日志，级别回滚 INFO 并修复滚动配置。",
                   0.76, "find /var/log/app -mtime +7 -delete")),
    dict(idx=6, sev="warning", svc="recommendation-service", cluster="prod-cluster-2", etype="cpu_throttling",
         tpl=CPU, raw="2026-09-11 04:18:37 WARN cpu throttle 34 percent rec-svc", fp="fp-8b66f1",
         topo="recommendation-service -> feature-store", hours=7.0, status="diagnosed", rag="success", score=0.68,
         cands=[("KB-006", 0.68)],
         ai=ai_out("推理批量过大导致 CFS 节流比例升高，可能是流量上涨或批处理配置回退。",
                   "降批量并观察节流比例，必要时扩容 limit。",
                   0.62, "")),
    dict(idx=7, sev="warning", svc="user-service", cluster="prod-cluster-1", etype="timeout",
         tpl=RDS_TMO, raw="2026-09-11 02:55:20 ERROR user-svc redis GET timeout 812 ms session:u77123", fp="fp-4c99a5",
         topo="user-service -> redis-session", hours=9.0, status="diagnosed", rag="success", score=0.72,
         cands=[("KB-007", 0.72), ("KB-002", 0.35)],
         ai=ai_out("Redis AOF rewrite fork 阻塞主线程，GET 排队超时。",
                   "rewrite 调度低峰执行，客户端超时降为 200ms 快速失败。",
                   0.85, "")),
    dict(idx=8, sev="critical", svc="api-gateway", cluster="prod-cluster-1", etype="gateway_502",
         tpl=G502, raw="2026-09-10 23:41:55 ERROR gateway upstream 502 payment-service after 980 ms", fp="fp-9f01a2",
         topo="api-gateway -> payment-service", hours=11.0, status="diagnosed", rag="success", score=0.80,
         cands=[("KB-001", 0.80), ("KB-008", 0.58)],
         ai=ai_out("payment-service 发布窗口内端点失效，与既往 502 案例一致。",
                   "发布完成后刷新网关上游；复用 KB-001 处置清单。",
                   0.87, "kubectl -n prod rollout status deploy/payment-service")),
    dict(idx=9, sev="warning", svc="notification-service", cluster="prod-cluster-2", etype="timeout",
         tpl=SMTP_TMO, raw="2026-09-10 21:30:11 WARN smtp timeout mx.mail.example 5012 ms", fp="fp-2d33b9",
         topo="notification-service -> smtp-gateway(外部)", hours=14.0, status="diagnosed", rag="success", score=0.75,
         cands=[("KB-010", 0.75)],
         ai=ai_out("邮件出口 IP 被对端限流，SMTP 握手超时。",
                   "启用备用 SMTP 通道并队列削峰重试。",
                   0.78, "")),
    dict(idx=10, sev="critical", svc="order-service", cluster="prod-cluster-1", etype="oom_killed",
         tpl=OOM, raw="2026-09-10 18:22:40 FATAL order-svc OOMKilled exit 137 limit 1024Mi", fp="fp-3d55c8",
         topo="order-service -> redis-cache", hours=18.0, status="diagnosed", rag="success", score=0.88,
         cands=[("KB-003", 0.88)],
         ai=ai_out("大促流量下缓存无上限增长再次触顶，与 KB-003 历史根因一致。",
                   "立即回滚缓存开关并按 KB-003 方案加 LRU 上限。",
                   0.93, "kubectl -n prod delete pod -l app=order-service")),
    dict(idx=11, sev="warning", svc="user-service", cluster="prod-cluster-1", etype="oom_killed",
         tpl=OOM, raw="2026-09-10 15:10:02 FATAL user-svc OOMKilled exit 137 limit 1024Mi", fp="fp-6e77c2",
         topo="user-service -> redis-session", hours=22.0, status="diagnosed", rag="success", score=0.76,
         cands=[("KB-012", 0.76)],
         ai=ai_out("会话序列化对象过大，反序列化峰值内存超限。",
                   "精简 session 字段并将 limit 提升至 1.5Gi。",
                   0.81, "")),
    dict(idx=12, sev="info", svc="api-gateway", cluster="staging-cluster", etype="gateway_502",
         tpl=G502, raw="2026-09-10 11:48:29 ERROR gateway upstream 502 payment-service after 764 ms", fp="fp-0a22d4",
         topo="api-gateway -> payment-service", hours=26.0, status="diagnosed", rag="success", score=0.71,
         cands=[("KB-001", 0.71)],
         ai=ai_out("staging 环境 payment-service 配置回滚导致端口不通。",
                   "核对 staging 发布版本并恢复配置。",
                   0.77, "")),
    dict(idx=13, sev="critical", svc="payment-service", cluster="prod-cluster-1", etype="timeout",
         tpl=PAY_TMO, raw="2026-09-10 08:03:47 WARN pay provider timeout 3540 ms order O-87902", fp="fp-7c22b3",
         topo="payment-service -> payment-provider(外部)", hours=30.0, status="diagnosed", rag="success", score=0.81,
         cands=[("KB-002", 0.81)],
         ai=ai_out("第三方通道批处理窗口超时，与 KB-002 记录吻合。",
                   "切换备用通道，超时 5s 并退避重试。",
                   0.89, "")),
    dict(idx=14, sev="warning", svc="recommendation-service", cluster="prod-cluster-2", etype="cpu_throttling",
         tpl=CPU, raw="2026-09-10 05:26:18 WARN cpu throttle 41 percent rec-svc", fp="fp-8b66f1",
         topo="recommendation-service -> feature-store", hours=34.0, status="diagnosed", rag="success", score=0.60,
         cands=[("KB-006", 0.60)],
         ai=ai_out("节流比例进一步升高，但缺少拓扑与部署变更佐证，置信度偏低。",
                   "核对近期变更单，降批量观察，必要时扩容。",
                   0.58, "")),
    # ---- RAG 成功但 LLM 降级 ----
    dict(idx=15, sev="critical", svc="payment-service", cluster="prod-cluster-1", etype="timeout",
         tpl=PAY_TMO, raw="2026-09-09 22:44:31 WARN pay provider timeout 3095 ms order O-87555", fp="fp-7c22b3",
         topo="payment-service -> payment-provider(外部)", hours=40.0, status="pending", rag="degraded", score=0.79,
         cands=[("KB-002", 0.79)], ai=None, degraded="llm_timeout"),
    dict(idx=16, sev="warning", svc="user-service", cluster="prod-cluster-1", etype="timeout",
         tpl=RDS_TMO, raw="2026-09-09 19:37:52 ERROR user-svc redis GET timeout 805 ms session:u74021", fp="fp-4c99a5",
         topo="user-service -> redis-session", hours=47.0, status="pending", rag="degraded", score=0.72,
         cands=[("KB-007", 0.72)], ai=None, degraded="invalid_json_output: 模型输出不是合法的诊断 JSON"),
    # ---- 未知告警 ----
    dict(idx=17, sev="warning", svc="notification-service", cluster="prod-cluster-2", etype=None,
         tpl="redis connection reset by peer after <NUM> ms",
         raw="2026-09-09 14:20:09 WARN redis conn reset by peer after 210 ms", fp="fp-b1c40e",
         topo="notification-service -> redis-notify", hours=55.0, status="unknown", rag="unknown", score=None,
         cands=[], ai=None, degraded="no_similar_case"),
    dict(idx=18, sev="warning", svc="order-service", cluster="prod-cluster-1", etype=None,
         tpl="GRPC unary call failed UNAVAILABLE upstream <SVC>",
         raw="2026-09-09 09:15:36 ERROR grpc UNAVAILABLE upstream order-inventory", fp="fp-c5d81a",
         topo="order-service -> inventory-service", hours=62.0, status="unknown", rag="unknown", score=None,
         cands=[], ai=None, degraded="no_similar_case"),
    dict(idx=19, sev="info", svc="user-service", cluster="prod-cluster-1", etype=None,
         tpl="certificate for <DOMAIN> expires in <NUM> days",
         raw="2026-09-08 20:02:18 INFO cert for api.example.com expires in 14 days", fp="fp-d9e23f",
         topo="user-service -> api.example.com", hours=70.0, status="unknown", rag="unknown", score=None,
         cands=[], ai=None, degraded="no_similar_case"),
    # ---- 待处理（未诊断） ----
    dict(idx=20, sev="critical", svc="api-gateway", cluster="prod-cluster-1", etype="gateway_502",
         tpl=G502, raw="2026-09-08 15:33:44 ERROR gateway upstream 502 payment-service after 1102 ms", fp="fp-9f01a2",
         topo="api-gateway -> payment-service", hours=80.0, status="pending", rag=None, score=None,
         cands=None, ai=None, degraded=None),
    dict(idx=21, sev="warning", svc="order-service", cluster="prod-cluster-1", etype="oom_killed",
         tpl=OOM, raw="2026-09-08 11:20:07 FATAL order-svc OOMKilled exit 137 limit 1024Mi", fp="fp-3d55c8",
         topo="order-service -> redis-cache", hours=88.0, status="pending", rag=None, score=None,
         cands=None, ai=None, degraded=None),
    dict(idx=22, sev="warning", svc="inventory-service", cluster="prod-cluster-2", etype="connection_refused",
         tpl=CONN, raw="2026-09-08 06:41:39 ERROR inv conn refused inventory-db:5432 retry 5", fp="fp-1e88d0",
         topo="inventory-service -> inventory-db", hours=96.0, status="pending", rag=None, score=None,
         cands=None, ai=None, degraded=None),
    dict(idx=23, sev="info", svc="log-collector", cluster="prod-cluster-1", etype="disk_full",
         tpl=DISK, raw="2026-09-07 21:18:55 WARN disk usage 91 percent on /var/log", fp="fp-5a11e7",
         topo="log-collector -> elasticsearch", hours=105.0, status="pending", rag=None, score=None,
         cands=None, ai=None, degraded=None),
    dict(idx=24, sev="critical", svc="payment-service", cluster="prod-cluster-1", etype="timeout",
         tpl=PAY_TMO, raw="2026-09-07 16:05:23 WARN pay provider timeout 3310 ms order O-87110", fp="fp-7c22b3",
         topo="payment-service -> payment-provider(外部)", hours=118.0, status="pending", rag=None, score=None,
         cands=None, ai=None, degraded=None),
    dict(idx=25, sev="warning", svc="user-service", cluster="prod-cluster-1", etype="timeout",
         tpl=RDS_TMO, raw="2026-09-07 09:47:12 ERROR user-svc redis GET timeout 798 ms session:u71205", fp="fp-4c99a5",
         topo="user-service -> redis-session", hours=130.0, status="pending", rag=None, score=None,
         cands=None, ai=None, degraded=None),
]


def build_event(spec: dict) -> Events:
    created = hours_ago(spec["hours"])
    std_ms = round(6.0 + (spec["idx"] % 5) * 1.7, 2)
    rag_ms = None
    if spec["cands"] is not None:
        rag_ms = round(38.0 + (spec["idx"] % 7) * 6.3, 2)
    ai = spec["ai"]
    confidence = ai["confidence"] if ai else None
    return Events(
        event_id=f"EVT-20260911-{spec['idx']:04d}",
        severity=spec["sev"],
        service_name=spec["svc"],
        cluster=spec["cluster"],
        error_type=spec["etype"],
        template=spec["tpl"],
        raw_log=spec["raw"],
        fingerprint=spec["fp"],
        topology=spec["topo"],
        status=spec["status"],
        rag_status=spec["rag"],
        rag_score=spec["score"],
        rag_ms=rag_ms,
        std_ms=std_ms,
        confidence=confidence,
        degraded_reason=spec.get("degraded"),
        candidates_json=json.dumps([cand(cid, sc) for cid, sc in spec["cands"]], ensure_ascii=False) if spec["cands"] else json.dumps([]),
        ai_output_json=json.dumps(ai, ensure_ascii=False) if ai else None,
        ai_root_cause=ai["root_cause"] if ai else None,
        ai_solution=ai["solution"] if ai else None,
        ai_command=ai.get("command") or None if ai else None,
        created_at=created,
        updated_at=created,
    )


# ---------------- 规则版本 ----------------

RULES_BASE = [
    dict(id="gateway_502", error_type="gateway_502", keywords=["bad gateway", "upstream error"], score=0.85, severity="critical"),
    dict(id="upstream_timeout", error_type="timeout", keywords=["timeout", "deadline exceeded"], score=0.75, severity="warning"),
    dict(id="oom_killed", error_type="oom_killed", keywords=["oomkilled", "out of memory"], score=0.9, severity="critical"),
    dict(id="connection_refused", error_type="connection_refused", keywords=["connection refused"], score=0.8, severity="warning"),
    dict(id="disk_full", error_type="disk_full", keywords=["no space left", "disk usage"], score=0.7, severity="warning"),
    dict(id="cpu_throttling", error_type="cpu_throttling", keywords=["cpu throttling"], score=0.65, severity="warning"),
]


def dump_rules(rules: list) -> str:
    lines = ["rules:"]
    for r in rules:
        lines.append(f"  - id: {r['id']}")
        lines.append(f"    error_type: {r['error_type']}")
        lines.append("    keywords:")
        for kw in r["keywords"]:
            lines.append(f"      - {kw}")
        lines.append(f"    score: {r['score']}")
        lines.append(f"    severity: {r['severity']}")
    return "\n".join(lines) + "\n"


RULES_V1 = dump_rules(RULES_BASE)
RULES_V2 = dump_rules(RULES_BASE + [
    dict(id="redis_pool_exhausted", error_type="timeout", keywords=["redis pool exhausted", "max active connections"], score=0.7, severity="warning"),
])
RULES_V3 = dump_rules(RULES_BASE + [
    dict(id="redis_pool_exhausted", error_type="timeout", keywords=["redis pool exhausted", "max active connections"], score=0.7, severity="warning"),
    dict(id="cert_expiring", error_type="cert_expiring", keywords=["certificate expires", "tls cert"], score=0.6, severity="info"),
])


# ---------------- 审批 / 变更集 ----------------

def cs_fields(case_id: str, **fields) -> dict:
    return {"case_id": case_id, **fields}


CHANGE_SETS = [
    dict(id=1, case_id="KB-002", change_type="update", status="published", version=2, approval=1,
         created_by=SRE, created_hours=47.0, reason="第三方通道超时阈值与重试策略更新（来自事件反馈人工修正）",
         before=snap("KB-002", version=1),
         after=snap("KB-002", version=2,
                    root_cause="第三方支付通道夜间批处理导致响应 P99 升至 3s 以上，触发 3s 超时熔断；高峰期叠加连接池等待。",
                    solution="1) 切换备用支付通道；2) 将超时阈值提升至 5s 并配置指数退避重试；3) 高峰期扩容连接池并监控等待队列。")),
    dict(id=2, case_id="KB-006", change_type="update", status="pending", version=3, approval=2,
         created_by=SRE, created_hours=6.0, reason="节流根因补充部署变更维度，原方案缺观察项",
         before=snap("KB-006", version=2),
         after=snap("KB-006", version=3,
                    root_cause="推荐模型推理批量过大叠加近期流量上涨，CPU limit 2 核下 CFS 节流比例超 30%，推理延迟抬升。",
                    solution="1) limit 提升至 4 核；2) 推理 batch size 降为 16；3) 观察节流比例回落至 10% 以下；4) 核对近 7 天部署变更记录。")),
    dict(id=3, case_id="KB-010", change_type="update", status="withdrawn", version=2, approval=5,
         created_by=SRE, created_hours=16.0, reason="备用 SMTP 通道信息待确认，先撤回",
         before=snap("KB-010", version=1),
         after=snap("KB-010", version=2, solution="1) 启用备用 SMTP 通道 smtp-backup；2) 队列削峰并退避重试；3) 申请对端白名单。")),
]

for cs in CHANGE_SETS:
    diff = {}
    for key, val in cs["after"].items():
        if cs["before"].get(key) != val:
            diff[key] = {"before": cs["before"].get(key), "after": val}
    cs["diff"] = diff

APPROVALS = [
    dict(id=1, applicant=SRE, role="sre", biz_type="kb_edit", biz_id="1", title="知识库变更：KB-002",
         reason="第三方通道超时阈值与重试策略更新", risk="medium", status="approved", cur=1, total=1,
         published=iso(46.0), created_hours=47.0,
         steps=[dict(no=1, role="approver", approver=LEAD, action="approve", comment="根因与处置方案完整，同意发布", acted=iso(46.0))]),
    dict(id=2, applicant=SRE, role="sre", biz_type="kb_edit", biz_id="2", title="知识库变更：KB-006",
         reason="节流根因补充部署变更维度", risk="medium", status="pending", cur=1, total=1,
         published=None, created_hours=6.0,
         steps=[dict(no=1, role="approver", approver=None, action="pending", comment=None, acted=None)]),
    dict(id=3, applicant=SRE, role="sre", biz_type="merge", biz_id="1", title="知识库去重合并：KB-004",
         reason="KB-009 与 KB-004 同签名同集群，合并至高反馈主案例", risk="medium", status="pending", cur=1, total=1,
         published=None, created_hours=8.0,
         steps=[dict(no=1, role="approver", approver=None, action="pending", comment=None, acted=None)]),
    dict(id=4, applicant=SRE, role="sre", biz_type="rule_promote", biz_id="2", title="未知告警晋升规则：#2",
         reason="将未知模板晋升为 grpc_unavailable 分类规则", risk="medium", status="rejected", cur=1, total=1,
         published=None, created_hours=21.0,
         steps=[dict(no=1, role="approver", approver=LEAD, action="reject", comment="GRPC 超时趋势需再观察一周，暂不晋升", acted=iso(20.0))]),
    dict(id=5, applicant=SRE, role="sre", biz_type="kb_edit", biz_id="3", title="知识库变更：KB-010",
         reason="备用 SMTP 通道信息补充", risk="low", status="withdrawn", cur=1, total=1,
         published=None, created_hours=16.5,
         steps=[dict(no=1, role="approver", approver=None, action="pending", comment=None, acted=None)]),
]

MERGE_PROPOSALS = [
    dict(id=1, master="KB-004", merged=["KB-009"], status="pending", approval=1, created_by=SRE, created_hours=8.0,
         reason="同签名同集群相似模板，合并主案例并归档冗余",
         strategy={"keep_fields": "master", "archive_redundant": True}),
]


# ---------------- 写入 ----------------

# 种子行使用显式主键，不会推进 PostgreSQL id 序列；写入完成后统一重置，
# 避免后续 ORM 插入（如 Agent 创建审批单/变更集）触发主键唯一冲突。
SEQ_TABLES = (
    "events", "kb_cases", "approval_requests", "approval_steps", "kb_change_sets",
    "kb_versions", "kb_merge_proposals", "rule_versions", "unknown_templates",
    "audit_logs", "console_configs",
)


async def _reset_id_sequences(db) -> None:
    """将各业务表 id 序列推进到 MAX(id)+1（与 scripts/fix_sequences.py 同语义）。"""
    for table in SEQ_TABLES:
        await db.execute(
            text(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                f"GREATEST(COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, 1), false)"
            )
        )
    await db.commit()


async def main() -> None:
    async with db_manager.session() as db:
        # 清空业务表（保持稳定顺序）
        for model in (Audit_logs, Approval_steps, Approval_requests, Kb_merge_proposals, Kb_change_sets,
                      Kb_versions, Unknown_templates, Rule_versions, Console_configs, Events, Kb_cases):
            await db.execute(delete(model))
        await db.commit()

        # 1. 配置中心
        configs = [Console_configs(config_key=k, config_value=v, created_at=hours_ago(300), updated_at=hours_ago(300))
                   for k, v in CONFIG_DEFAULTS.items()]
        db.add_all(configs)

        # 2. 知识案例
        case_rows = []
        for i, c in enumerate(KB_CASES):
            case_rows.append(Kb_cases(**c, created_at=hours_ago(280 - i * 4), updated_at=hours_ago(48 - (i % 20))))
        db.add_all(case_rows)

        # 3. 历史版本快照
        versions = [
            ("KB-001", 1, 260.0, ADMIN), ("KB-001", 2, 140.0, SRE), ("KB-001", 3, 24.0, LEAD),
            ("KB-002", 1, 210.0, ADMIN), ("KB-002", 2, 46.0, LEAD),
            ("KB-003", 1, 190.0, ADMIN), ("KB-003", 2, 96.0, SRE),
            ("KB-004", 1, 170.0, ADMIN), ("KB-004", 2, 72.0, SRE),
            ("KB-005", 1, 150.0, SRE), ("KB-005", 2, 60.0, SRE),
            ("KB-006", 1, 130.0, SRE), ("KB-006", 2, 40.0, SRE),
        ]
        for case_id, ver, hrs, author in versions:
            db.add(Kb_versions(case_id=case_id, version=ver, snapshot_json=json.dumps(snap(case_id, version=ver), ensure_ascii=False),
                               created_by=author, created_at=hours_ago(hrs), updated_at=hours_ago(hrs)))

        # 4. 事件
        db.add_all([build_event(spec) for spec in EVENTS_SPEC])

        # 5. 变更集
        for cs in CHANGE_SETS:
            db.add(Kb_change_sets(
                case_id=cs["case_id"], change_type=cs["change_type"],
                before_json=json.dumps(cs["before"], ensure_ascii=False),
                after_json=json.dumps(cs["after"], ensure_ascii=False),
                diff_json=json.dumps(cs["diff"], ensure_ascii=False),
                reason=cs["reason"], status=cs["status"], version=cs["version"],
                approval_request_id=cs["approval"], created_by=cs["created_by"],
                created_at=hours_ago(cs["created_hours"]), updated_at=hours_ago(cs["created_hours"]),
            ))

        # 6. 审批单与步骤
        for req in APPROVALS:
            created = hours_ago(req["created_hours"])
            db.add(Approval_requests(
                id=req["id"], applicant=req["applicant"], applicant_role=req["role"], biz_type=req["biz_type"],
                biz_id=req["biz_id"], title=req["title"], reason=req["reason"], risk_level=req["risk"],
                status=req["status"], current_step=req["cur"], total_steps=req["total"],
                published_at=req["published"], created_at=created, updated_at=created,
            ))
            for st in req["steps"]:
                db.add(Approval_steps(request_id=req["id"], step_no=st["no"], approver_role=st["role"],
                                      approver=st["approver"], action=st["action"], comment=st["comment"],
                                      acted_at=st["acted"], created_at=created, updated_at=created))

        # 7. 合并提案
        for mp in MERGE_PROPOSALS:
            db.add(Kb_merge_proposals(
                master_case_id=mp["master"], merged_case_ids=json.dumps(mp["merged"]),
                merge_strategy_json=json.dumps(mp["strategy"]), reason=mp["reason"],
                status=mp["status"], approval_request_id=mp["approval"], created_by=mp["created_by"],
                created_at=hours_ago(mp["created_hours"]), updated_at=hours_ago(mp["created_hours"]),
            ))

        # 8. 规则版本
        db.add_all([
            Rule_versions(version=1, content=RULES_V1, status="superseded", change_note="初始 6 条内置分类规则",
                          created_by=ADMIN, created_at=hours_ago(300), updated_at=hours_ago(300)),
            Rule_versions(version=2, content=RULES_V2, status="superseded", change_note="新增 redis_pool_exhausted 规则",
                          created_by=ADMIN, created_at=hours_ago(200), updated_at=hours_ago(200)),
            Rule_versions(version=3, content=RULES_V3, status="active", change_note="新增 cert_expiring 预警规则，gateway_502 关键词修正",
                          created_by=ADMIN, created_at=hours_ago(24), updated_at=hours_ago(24)),
        ])

        # 9. 未知模板
        db.add_all([
            Unknown_templates(template="redis connection reset by peer after <NUM> ms", suggested_error_type=None,
                              sample_count=23, last_seen_service="notification-service", status="pending",
                              created_at=hours_ago(30), updated_at=hours_ago(2)),
            Unknown_templates(template="GRPC unary call failed UNAVAILABLE upstream <SVC>", suggested_error_type="grpc_unavailable",
                              sample_count=12, last_seen_service="order-service", status="pending",
                              created_at=hours_ago(48), updated_at=hours_ago(20)),
            Unknown_templates(template="pod <NAME> evicted due to node memory pressure", suggested_error_type="oom_evict",
                              sample_count=8, last_seen_service="order-service", status="promoted",
                              created_at=hours_ago(120), updated_at=hours_ago(96)),
            Unknown_templates(template="certificate for <DOMAIN> expires in <NUM> days", suggested_error_type="cert_expiring",
                              sample_count=3, last_seen_service="api-gateway", status="discarded",
                              created_at=hours_ago(150), updated_at=hours_ago(130)),
        ])

        # 10. 审计日志
        audits = [
            (0.3, ADMIN, "diagnosis_run", "event", "1", None, {"model": "deepseek-v4-flash", "rag_score": 0.82, "confidence": 0.88, "low_confidence": False}),
            (2.0, ADMIN, "config_update", "console_config", "confidence_threshold", {"value": None}, {"value": "0.75"}),
            (8.0, SRE, "merge_proposal_create", "kb_merge_proposal", "1", None, {"master": "KB-004", "merged": ["KB-009"], "approval_id": 3}),
            (10.0, OPERATOR, "feedback", "kb_case", "KB-001", {"feedback_score": 1.0, "event_id": 1}, {"feedback_score": 2.0, "rating": "up"}),
            (20.0, LEAD, "approval_reject", "approval_request", "4", {"status": "pending"}, {"status": "rejected", "comment": "GRPC 超时趋势需再观察一周"}),
            (21.0, SRE, "unknown_promote_request", "unknown_template", "2", None, {"error_type": "grpc_unavailable", "approval_id": 4}),
            (24.0, ADMIN, "rule_reload", "rule_version", "3", None, {"version": 3, "rule_count": 8, "note": "新增 cert_expiring 预警规则"}),
            (46.0, LEAD, "semantic_cache_invalidate", "kb_cache", "kb:KB-002", None, {"reason": "kb_publish", "version": 2}),
            (46.0, LEAD, "kb_publish", "kb_case", "KB-002", None, {"version": 2, "change_set_id": 1, "approval_id": 1}),
            (46.5, LEAD, "approval_approve", "approval_request", "1", None, {"status": "approved", "outcome": {"type": "kb_publish", "case_id": "KB-002", "version": 2}}),
            (100.0, ADMIN, "kb_rollback", "kb_case", "KB-001", {"restored_from_version": 2}, {"version": 3}),
        ]
        for hrs, actor, action, ttype, tid, before, after in audits:
            db.add(Audit_logs(actor=actor, action=action, target_type=ttype, target_id=tid,
                              before_json=json.dumps(before, ensure_ascii=False) if before else None,
                              after_json=json.dumps(after, ensure_ascii=False) if after else None,
                              created_at=hours_ago(hrs), updated_at=hours_ago(hrs)))

        await db.commit()
        await _reset_id_sequences(db)

        # 验证
        print("=== 种子数据写入完成 ===")
        for label, model in [("events", Events), ("kb_cases", Kb_cases), ("approval_requests", Approval_requests),
                             ("approval_steps", Approval_steps), ("kb_change_sets", Kb_change_sets),
                             ("kb_versions", Kb_versions), ("rule_versions", Rule_versions),
                             ("unknown_templates", Unknown_templates), ("audit_logs", Audit_logs),
                             ("console_configs", Console_configs), ("kb_merge_proposals", Kb_merge_proposals)]:
            n = (await db.execute(select(func.count()).select_from(model))).scalar()
            print(f"{label} = {n}")


if __name__ == "__main__":
    asyncio.run(main())
