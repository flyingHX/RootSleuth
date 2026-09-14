"""统一日志：优先读取 config/logging.yaml，失败则回退到基础配置。"""
import logging
import logging.config
from pathlib import Path


def setup_logging(config_path: str = "config/logging.yaml") -> None:
    try:
        import yaml
        cfg_path = Path(config_path)
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            Path("logs").mkdir(exist_ok=True)
            logging.config.dictConfig(cfg)
            return
    except Exception:  # noqa: BLE001
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
