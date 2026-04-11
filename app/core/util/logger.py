# -*- coding: utf-8 -*-
"""
日志模块 v2 — 可查询的统一日志系统

【一体化设计】
  写: setup_logger() / get_logger() / LogMixin → 控制台输出
  读: query_logs() / get_recent_logs() / get_error_logs() → Agent 查询
  管: install_buffer() / buffer_stats() / clear_logs() → 运行时管理

Agent 使用方式:
    from app.core.util.logger import query_logs, get_error_logs, get_recent_logs
    
    # 查最近错误
    errors = get_error_logs(20)
    
    # 按条件查
    logs = query_logs(level="ERROR", keyword="组件", since="5m")
    
    # API: GET /api/logs
"""

import logging
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

LOG_LEVELS = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
    'CRITICAL': logging.CRITICAL
}

_loggers: dict = {}


# ============================================================
# LogEntry + LogBuffer（内存捕获器）
# ============================================================

@dataclass
class LogEntry:
    """单条日志记录"""
    timestamp: float
    level: str
    logger_name: str
    message: str
    module: str = ""
    func_name: str = ""
    line_no: int = 0
    thread: str = ""

    @property
    def time_str(self) -> str:
        return datetime.fromtimestamp(self.timestamp).strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self) -> dict:
        return {
            "time": self.time_str,
            "timestamp": self.timestamp,
            "level": self.level,
            "logger": self.logger_name,
            "message": self.message,
            "module": self.module,
        }

    def __str__(self) -> str:
        return f"[{self.time_str}] [{self.level:>7}] {self.logger_name}: {self.message}"


class _LogBufferHandler(logging.Handler):
    """
    内存环形缓冲 Handler（内部类）
    
    作为第二个 handler 安装到 logger 上，
    在输出到控制台的同时将每条日志存入内存 Ring Buffer。
    """

    _instance: Optional["_LogBufferHandler"] = None

    def __new__(cls, max_size=5000):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, max_size=5000):
        if getattr(self, '_initialized', False):
            return
        self._initialized = True
        super().__init__(level=logging.DEBUG)
        self._max_size = max_size
        self._buffer: deque = deque(maxlen=max_size)
        self._lock = threading.RLock()
        self._installed = False
        self._total_captured = 0
        self.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        ))

    def emit(self, record):
        try:
            entry = LogEntry(
                timestamp=record.created,
                level=record.levelname,
                logger_name=record.name,
                message=record.getMessage(),
                module=getattr(record, 'module', '') or '',
                func_name=getattr(record, 'funcName', '') or '',
                line_no=getattr(record, 'lineno', 0) or 0,
                thread=threading.current_thread().name,
            )
            with self._lock:
                self._buffer.append(entry)
                self._total_captured += 1
        except Exception:
            pass  # 日志系统自身不能抛异常

    # ---- 安装/卸载 ----

    def install(self, target=None):
        if self._installed:
            return
        target = target or logging.getLogger()
        if self not in target.handlers:
            target.addHandler(self)
            self._installed = True
            logging.getLogger(__name__).info(
                f"[LogBuffer] 已安装 (容量={self._max_size})")

    def uninstall(self, target=None):
        target = target or logging.getLogger()
        if self in target.handlers:
            target.removeHandler(self)
            self._installed = False

    @property
    def installed(self) -> bool:
        return self._installed

    # ---- 查询 ----

    def query(self, level=None, logger_name=None, keyword=None,
              since=None, until=None, limit=100, offset=0, reverse=True):
        now = time.time()
        since_ts = self._parse_offset(since, now) if since else 0
        until_ts = self._parse_offset(until, now) if until else now + 3600

        result = []
        with self._lock:
            for e in self._buffer:
                if e.timestamp < since_ts or e.timestamp > until_ts:
                    continue
                if level and e.level.upper() != level.upper():
                    continue
                if logger_name and logger_name.lower() not in e.logger_name.lower():
                    continue
                if keyword and keyword.lower() not in e.message.lower():
                    continue
                result.append(e)

        if reverse:
            result.reverse()
        return result[offset:offset + limit]

    def recent(self, n=50):
        with self._lock:
            entries = list(self._buffer)
        return entries[-n:] if len(entries) >= n else entries[-len(entries):] if entries else []

    def stats(self):
        with self._lock:
            levels = {}
            for e in self._buffer:
                levels[e.level] = levels.get(e.level, 0) + 1
            return {
                "capacity": self._max_size,
                "size": len(self._buffer),
                "utilization_pct": round(len(self._buffer) / self._max_size * 100, 1),
                "total_captured": self._total_captured,
                "installed": self._installed,
                "levels": levels,
                "latest": self._buffer[-1].to_dict() if self._buffer else None,
            }

    def clear(self):
        with self._lock:
            self._buffer.clear()

    @staticmethod
    def _parse_offset(s, now):
        s = s.strip().lower()
        for unit, mul in [("s", 1), ("m", 60), ("h", 3600), ("d", 86400)]:
            if s.endswith(unit):
                try:
                    return now - float(s[:-len(unit)]) * mul
                except ValueError:
                    break
        return 0

    def export_text(self, level=None, limit=200):
        entries = self.query(level=level, limit=limit)
        return "\n".join(str(e) for e in entries)


# ============================================================
# 全局便捷函数（Agent 直接调用）
# ============================================================

def get_buffer() -> _LogBufferHandler:
    """获取全局 LogBuffer 实例"""
    return _LogBufferHandler()


def install_buffer(max_size=5000):
    """安装内存日志捕获器到 root logger（main.py 调用一次）"""
    buf = _LogBufferHandler(max_size=max_size)
    buf.install()


def query_logs(**kwargs) -> List[Dict]:
    """查询运行日志
    
    参数:
        level: DEBUG/INFO/WARNING/ERROR/CRITICAL
        logger_name: logger 名称过滤
        keyword: 关键词搜索
        since: 时间偏移 ("5m", "1h", "30s")
        limit: 最大条数
    返回: [dict, ...]
    """
    buf = get_buffer()
    return [e.to_dict() for e in buf.query(**kwargs)]


def get_recent_logs(n=50) -> List[Dict]:
    """获取最近 N 条日志"""
    buf = get_buffer()
    return [e.to_dict() for e in buf.recent(n)]


def get_error_logs(n=50) -> List[Dict]:
    """获取最近的错误日志"""
    buf = get_buffer()
    return [e.to_dict() for e in buf.query(level="ERROR", limit=n)]


def buffer_stats() -> Dict:
    """日志缓冲区统计"""
    return get_buffer().stats()


def clear_logs() -> int:
    """清空日志缓冲区，返回清理条数"""
    buf = get_buffer()
    size = len(buf._buffer)  # noqa
    buf.clear()
    return size


# ============================================================
# 原有功能保留（写日志）
# ============================================================

def _load_logging_config():
    """从 AgentConfig 读取 logging 配置"""
    try:
        from app.core.util.agent_config import get_config
        cfg = get_config()
        return {
            "level": cfg.get("logging.level", "INFO"),
            "format": cfg.get("logging.format", "%(asctime)s [%(levelname)s] %(name)s: %(message)s"),
        }
    except Exception:
        return {"level": "INFO", "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s"}


def setup_logger(
    name: str = "app",
    level: Optional[str] = None,
    log_format: Optional[str] = None
) -> logging.Logger:
    """设置并获取日志器（同时安装 LogBuffer 如果已初始化）"""
    if name in _loggers:
        return _loggers[name]

    config = _load_logging_config()
    if level is None:
        level = config["level"]
    if log_format is None:
        log_format = config["format"]

    numeric_level = LOG_LEVELS.get(level.upper(), logging.INFO)

    logger_obj = logging.getLogger(name)
    logger_obj.setLevel(numeric_level)

    # 控制台输出
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(numeric_level)
    formatter = logging.Formatter(log_format)
    handler.setFormatter(formatter)

    if not logger_obj.handlers:
        logger_obj.addHandler(handler)

    logger_obj.propagate = False
    _loggers[name] = logger_obj

    return logger_obj


def get_logger(name: str) -> logging.Logger:
    """获取已配置的日志器"""
    if name not in _loggers:
        return setup_logger(name)
    return _loggers[name]


class LogMixin:
    """日志混入类 — 为任意类提供 self.logger 属性"""

    @property
    def logger(self) -> logging.Logger:
        if not hasattr(self, '_logger'):
            self._logger = get_logger(self.__class__.__name__)
        return self._logger
