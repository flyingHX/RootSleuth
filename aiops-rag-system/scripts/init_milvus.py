"""初始化 Milvus 集合：创建 aiops_knowledge_base、向量/标量索引与近 3 个月分区。

用法:
    python scripts/init_milvus.py               # 幂等创建（已存在则跳过）
    python scripts/init_milvus.py --overwrite   # 危险：删除并重建集合
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config  # noqa: E402
from src.rag_pipeline.milvus_client import MilvusClient  # noqa: E402


def main() -> None:
    overwrite = "--overwrite" in sys.argv
    config = load_config(str(PROJECT_ROOT))
    client = MilvusClient(config["milvus"])
    if client.ensure_collection(overwrite=overwrite):
        print(
            f"[OK] Collection '{config['milvus']['collection']}' ready "
            f"at {config['milvus']['host']}:{config['milvus']['port']}"
        )
    else:
        print("[FAIL] 集合初始化失败，请检查 Milvus 连接（docker compose up -d milvus）")
        sys.exit(1)


if __name__ == "__main__":
    main()
