import logging
import os
import re

DEFAULT_LOG_BACKUP_COUNT = 10
DEFAULT_LOG_MAX_TOTAL_MB = 10
LOG_CLEANUP_DISABLED_VALUES = {"0", "false", "no", "off"}
LOG_FILE_PATTERN = re.compile(r"^app_(\d{8})(?:_(\d{6}))?\.log$")


def _log_file_sort_key(filename: str) -> tuple[str, str]:
    """Sort both app_YYYYMMDD.log and legacy app_YYYYMMDD_HHMMSS.log names."""
    match = LOG_FILE_PATTERN.match(filename)
    if not match:
        return "", ""
    log_date, log_time = match.groups()
    # Daily aggregate logs should be considered the newest file for that day.
    return log_date, log_time or "999999"


def cleanup_old_log_files(log_dir: str, current_log_file: str | None = None):
    """Keep recent app_*.log files by filename date to bound local preview logs."""
    if os.environ.get("FUNCSEA_BACKEND_LOG_CLEANUP_ENABLED", "true").lower() in LOG_CLEANUP_DISABLED_VALUES:
        return

    try:
        backup_count = int(os.environ.get("FUNCSEA_BACKEND_LOG_BACKUP_COUNT", str(DEFAULT_LOG_BACKUP_COUNT)))
    except ValueError:
        backup_count = DEFAULT_LOG_BACKUP_COUNT

    try:
        max_total_mb = float(os.environ.get("FUNCSEA_BACKEND_LOG_MAX_TOTAL_MB", str(DEFAULT_LOG_MAX_TOTAL_MB)))
        max_total_bytes = int(max_total_mb * 1024 * 1024)
    except ValueError:
        max_total_bytes = DEFAULT_LOG_MAX_TOTAL_MB * 1024 * 1024

    if backup_count < 0:
        return

    try:
        # The active FileHandler has already opened this file; never delete it.
        current_log_path = os.path.abspath(current_log_file) if current_log_file else None
        log_files = [
            entry
            for entry in os.scandir(log_dir)
            if entry.is_file() and LOG_FILE_PATTERN.match(entry.name) and os.path.abspath(entry.path) != current_log_path
        ]
        log_files.sort(key=lambda entry: _log_file_sort_key(entry.name), reverse=True)
        total_size = 0
        for index, entry in enumerate(log_files):
            file_size = entry.stat().st_size
            if index >= backup_count or (max_total_bytes >= 0 and total_size + file_size > max_total_bytes):
                os.remove(entry.path)
                continue
            total_size += file_size
    except Exception:
        logging.getLogger(__name__).warning("Failed to cleanup old log files", exc_info=True)
