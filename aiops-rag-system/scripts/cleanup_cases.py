"""季度清理脚本：扫描并删除近似重复的冗余案例（余弦相似度 > 阈值）。

策略：相似案例对中保留 feedback_score 更高的一条，删除另一条。

用法:
    python scripts/cleanup_cases.py --dry-run   # 仅预览，不删除
    python scripts/cleanup_cases.py             # 执行删除
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config  # noqa: E402
from src.rag_pipeline.milvus_client import MilvusClient  # noqa: E402


def main() -> None:
    config = load_config(str(PROJECT_ROOT))
    milvus = MilvusClient(config["milvus"])

    pairs = milvus.find_near_duplicates(threshold=0.99, sample_limit=1000)
    if not pairs:
        print("[OK] 未发现近似重复案例，无需清理")
        return

    print(f"发现 {len(pairs)} 对近似重复案例：")
    for keep_id, drop_id, sim in pairs:
        print(f"  保留 {keep_id}  删除 {drop_id}  相似度={sim:.4f}")

    if "--dry-run" in sys.argv:
        print("[DRY-RUN] 仅预览，未执行删除")
        return

    drop_ids = [drop_id for _, drop_id, _ in pairs]
    if milvus.delete_cases(drop_ids):
        print(f"[OK] 已删除 {len(drop_ids)} 条冗余案例")
    else:
        print("[FAIL] 删除失败，请检查 Milvus 连接")
        sys.exit(1)


if __name__ == "__main__":
    main()
