"""知识库去重合并相似度阈值（≥80% 才展示合并建议）专项测试。

覆盖 services.console_kb.scan_duplicates 的展示门槛语义：
- 模板相似度 = 80%（阈值边界）且同错误类型 + 同服务 → 纳入同一合并组；
- 模板相似度不足 80%（如 0.6 / 0.0）即使同集群也不再并入（移除同集群兜底口径）；
- 已提交合并审批（pending 提案）的案例不再重复统计，提案拒绝后恢复可扫；
- 组内返回两两相似度 similarities 与组内最低相似度 min_pair_similarity。
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.database import Base
from models import kb_cases, kb_merge_proposals  # noqa: F401  注册表
from models.kb_cases import Kb_cases
from models.kb_merge_proposals import Kb_merge_proposals
from services.console_kb import KB_DEDUP_SIMILARITY_THRESHOLD, scan_duplicates


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


def _case(case_id: str, template: str, cluster: str) -> Kb_cases:
    return Kb_cases(
        case_id=case_id,
        alert_template=template,
        error_type="memory",
        service_name="orders",
        cluster=cluster,
        status="active",
        version=1,
    )


@pytest.mark.asyncio
async def test_scan_duplicates_threshold_80_percent(db_session):
    """相似度恰好 80% 的同签名对并入合并组；60% 的同集群对不再并入。"""
    db_session.add_all(
        [
            # A 与 B：交集 4 / 并集 5 = 0.8，恰好达到阈值（不同集群也并入）
            _case("KB-A", "aaa bbb ccc ddd", "c1"),
            _case("KB-B", "aaa bbb ccc ddd eee", "c2"),
            # C 与 A：交集 3 / 并集 5 = 0.6，低于阈值；即使同集群也不展示
            _case("KB-C", "aaa bbb ccc eee", "c1"),
        ]
    )
    await db_session.commit()

    groups = await scan_duplicates(db_session)

    assert KB_DEDUP_SIMILARITY_THRESHOLD == 0.8
    assert len(groups) == 1
    group = groups[0]
    assert group["case_ids"] == ["KB-A", "KB-B"]
    assert group["suggested_master"] in {"KB-A", "KB-B"}
    assert group["similarities"]["KB-A"]["KB-B"] == pytest.approx(0.8)
    assert group["min_pair_similarity"] == pytest.approx(0.8)


@pytest.mark.asyncio
async def test_scan_duplicates_low_similarity_not_grouped(db_session):
    """相似度为 0 的同签名案例即使同集群、同服务也不产生合并建议。"""
    db_session.add_all(
        [
            _case("KB-X", "OOMKilled memory cgroup limit exceeded", "c1"),
            _case("KB-Y", "Connection refused timeout socket", "c1"),
        ]
    )
    await db_session.commit()

    groups = await scan_duplicates(db_session)
    assert groups == []


@pytest.mark.asyncio
async def test_scan_duplicates_excludes_pending_proposal_cases(db_session):
    """已提交合并审批（pending 提案）的案例不再重复统计；提案拒绝后恢复可扫。"""
    db_session.add_all(
        [
            _case("KB-M", "aaa bbb ccc ddd", "c1"),
            _case("KB-N", "aaa bbb ccc ddd eee", "c1"),
        ]
    )
    await db_session.commit()

    # 提交合并审批前：扫描命中 1 组
    groups = await scan_duplicates(db_session)
    assert [g["case_ids"] for g in groups] == [["KB-M", "KB-N"]]

    # 提交合并审批（pending 提案）后：主案例与被合并案例均不再被统计
    proposal = Kb_merge_proposals(
        master_case_id="KB-M",
        merged_case_ids='["KB-N"]',
        status="pending",
        created_by="sre@atoms.dev",
    )
    db_session.add(proposal)
    await db_session.commit()
    assert await scan_duplicates(db_session) == []

    # 审批拒绝：提案脱离 pending，案例恢复可扫并重新成组
    proposal.status = "rejected"
    await db_session.commit()
    groups = await scan_duplicates(db_session)
    assert [g["case_ids"] for g in groups] == [["KB-M", "KB-N"]]
