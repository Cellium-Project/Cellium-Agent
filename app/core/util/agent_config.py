# -*- coding: utf-8 -*-
"""
Agent 配置管理器（多文件合并 + 热加载版）

设计原则：
  1. 配置按职责拆分为独立 YAML 文件（config/agent/*.yaml）
  2. 启动时一次性加载所有配置并深度合并
  3. 运行时通过 mtime 检测文件变更，支持自动/手动热重载
  4. 变更时通过回调通知订阅者（LLM引擎、安全模块等）

目录结构：
  config/
    agent/
      server.yaml      ← 服务端（需重启生效）
      llm.yaml          ← LLM 引擎（热重载）
      agent.yaml        ← Agent 行为（热重载）
      routes.yaml       ← 路由（需重启）
      memory.yaml       ← 记忆系统（热重载）
      security.yaml     ← 安全策略（热重载！紧急封禁）
      logging.yaml      ← 日志（热重载）
      channels.yaml     ← 通道配置（需重启生效）
"""

import os
import pathlib
import re
import threading
from typing import Any, Callable, Dict, List, Optional

try:
    import yaml
except ImportError:
    yaml = None


# ============================================================
# 回调类型
# ============================================================
ConfigChangeCallback = Callable[[str, Dict[str, Any], Dict[str, Any]], None]
"""配置变更回调: callback(section_name, old_value, new_value)"""


class AgentConfig:
    """
    多文件热加载配置管理器

    用法:
        from app.core.util.agent_config import get_config

        config = get_config()

        # 读取
        config.get("llm.openai.model")           # → "gpt-4o"
        config.get_section("security")            # → dict

        # 热重载
        config.reload()                           # 手动全量重载
        config.reload_section("security")         # 仅重载安全配置
        config.auto_reload = True                 # 开启文件监听（每次读取前检查）

        # 监听变更
        config.on_change("llm", my_callback)      # LLM 配置变更时触发
    """

    _instance: Optional["AgentConfig"] = None

    # 默认加载的配置文件列表（顺序决定合并优先级，后覆盖前）
    DEFAULT_FILES = [
        "server.yaml",
        "llm.yaml",
        "agent.yaml",
        "routes.yaml",
        "memory.yaml",
        "security.yaml",
        "logging.yaml",
        "learning.yaml",
        "heuristics.yaml",
        "channels.yaml",
    ]

    def __new__(cls, config_dir: Optional[pathlib.Path] = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, config_dir: Optional[pathlib.Path] = None):
        if self._initialized:
            return
        self._initialized = True

        # 配置目录: 项目根目录/config/agent/
        if config_dir is None:
            base_dir = pathlib.Path(__file__).resolve().parent.parent.parent.parent
            self._config_dir = base_dir / "config" / "agent"
        else:
            self._config_dir = config_dir

        # 合并后的完整配置
        self._config: Dict[str, Any] = {}

        # 各文件的最后修改时间（用于变更检测）
        self._mtimes: Dict[str, float] = {}

        # 各段对应的源文件映射 {section_key: filename}
        self._section_sources: Dict[str, str] = {}

        # 变更回调 {section_name: [callback, ...]}
        self._callbacks: Dict[str, List[ConfigChangeCallback]] = {}

        # 自动重载开关（每次 get() 前检测文件变更）
        self._auto_reload = False

        # 锁（线程安全）
        self._lock = threading.RLock()

        # 首次加载
        self._load_all()
        self._logger = self._get_logger()

    # ---- 加载逻辑 ----

    def _load_all(self):
        """加载并合并所有配置文件"""
        merged: Dict[str, Any] = {}
        new_mtimes: Dict[str, float] = {}
        new_section_sources: Dict[str, str] = {}

        for filename in self.DEFAULT_FILES:
            filepath = self._config_dir / filename
            if not filepath.exists():
                continue

            try:
                file_config = self._load_single_file(filepath)

                if file_config:
                    new_mtimes[filename] = filepath.stat().st_mtime

                    # 记录每个顶层 key 来自哪个文件
                    for key in file_config:
                        new_section_sources[key] = filename

                    # 深度合并
                    merged = self._deep_merge(merged, file_config)

            except Exception as e:
                logger = self._get_logger()
                if logger:
                    logger.error(f"[AgentConfig] 加载 {filename} 失败: {e}")

        with self._lock:
            old_config = self._config
            self._config = merged or self._defaults()
            self._mtimes = new_mtimes
            self._section_sources = new_section_sources

        return old_config

    def _load_single_file(self, filepath: pathlib.Path) -> Optional[Dict]:
        """加载单个 YAML 文件"""
        if not filepath.exists():
            return None

        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        if yaml is None:
            raise RuntimeError("PyYAML 未安装")

        raw = yaml.safe_load(content)
        return self._resolve_env_vars(raw) if raw else None

    @staticmethod
    def _deep_merge(base: Dict, override: Dict) -> Dict:
        """深度合并字典，override 覆盖 base"""
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = AgentConfig._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    def _resolve_env_vars(self, obj: Any) -> Any:
        """递归解析 ${VAR} 和 ${VAR:default} 环境变量引用"""
        if isinstance(obj, str):
            def _replace(match):
                var_name = match.group(1)
                default = match.group(2) or ""
                return os.environ.get(var_name, default)
            return re.sub(r"\$\{(\w+)(?::([^}]*))?\}", _replace, obj)
        elif isinstance(obj, dict):
            return {k: self._resolve_env_vars(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._resolve_env_vars(item) for item in obj]
        return obj

    @staticmethod
    def _defaults() -> Dict[str, Any]:
        """默认配置兜底"""
        return {
            "server": {"host": "0.0.0.0", "port": 8000,
                       "cors": {"enabled": True, "allow_origins": ["*"]}},
            "llm": {"provider": "openai",
                    "openai": {"model": "gpt-4o", "temperature": 0.7},
                    "streaming": {"enabled": True}},
            "agent": {
                "max_iterations": 10,
                "enforce_iteration_limit": False,  # 默认不限制迭代次数
                "request_timeout": 300,
                "flash_mode": False
            },
            "routes": {"chat": {"enabled": True, "path": "/api/chat"},
                      "health": {"enabled": True, "path": "/api/health"}},
            "memory": {"memory_dir": "memory"},
            "security": {"permission_level": "standard", "command_timeout": 30},
            "logging": {"level": "INFO", "console": True},
        }

    # ---- 公共读取接口 ----

    def get(self, key_path: str, default: Any = None) -> Any:
        """
        获取配置值（支持点号路径）

        当 auto_reload=True 时，每次读取前自动检测文件变更。
        """
        self._check_auto_reload()

        keys = key_path.split(".")
        value = self._config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value

    def get_section(self, section: str) -> Dict[str, Any]:
        """获取整个配置段的副本"""
        self._check_auto_reload()
        section_data = self._config.get(section, {})
        import copy
        return copy.deepcopy(section_data) if isinstance(section_data, dict) else {}

    @property
    def raw(self) -> Dict[str, Any]:
        """完整配置深拷贝"""
        import copy
        return copy.deepcopy(self._config)

    @property
    def sections(self) -> List[str]:
        """已加载的配置段名称列表"""
        return list(self._config.keys())

    @property
    def config_dir(self) -> pathlib.Path:
        """获取 agent 配置目录 (config/agent/)"""
        return self._config_dir

    @property
    def config_root(self) -> pathlib.Path:
        """获取配置根目录 (config/)"""
        return self._config_dir.parent

    # ---- 写入接口 ----

    def set(self, key_path: str, value: Any, persist: bool = False):
        """
        运行时动态修改配置值

        Args:
            key_path: 点号路径，如 "llm.openai.model"
            value: 新值
            persist: 是否写回对应 YAML 文件（默认仅内存修改）
        """
        keys = key_path.split(".")
        top_key = keys[0]
        
        with self._lock:
            old_value = self._config.get(top_key)
            if isinstance(old_value, dict):
                import copy
                old_value = copy.deepcopy(old_value)
            
            target = self._config
            for k in keys[:-1]:
                if k not in target or not isinstance(target[k], dict):
                    target[k] = {}
                target = target[k]
            target[keys[-1]] = value
            
            new_value = self._config.get(top_key)

        if persist:
            self._persist_to_file(key_path, value)
        
        self._notify_callbacks({top_key: ("updated", old_value, new_value)})

    def _persist_to_file(self, key_path: str, value: Any):
        """将修改写回对应的 YAML 文件（深度合并而非整体替换）"""
        top_key = key_path.split(".")[0]
        source_file = self._section_sources.get(top_key)
        if not source_file:
            return

        filepath = self._config_dir / source_file
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            raw = yaml.safe_load(content) or {}
            existing_section = raw.get(top_key, {})
            if isinstance(existing_section, dict) and isinstance(value, dict):
                merged = self._deep_merge(existing_section, value)
                raw[top_key] = merged
            else:
                self._set_nested(raw, key_path.split("."), value)
            with open(filepath, "w", encoding="utf-8") as f:
                yaml.dump(raw, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        except Exception as e:
            if self._logger:
                self._logger.error(f"[AgentConfig] 写回配置失败 {key_path}: {e}")

    @staticmethod
    def _set_nested(d: Dict, keys: List[str], value: Any):
        for k in keys[:-1]:
            d = d.setdefault(k, {})
        d[keys[-1]] = value

    # ---- 重载机制 ----

    def reload(self) -> Dict[str, str]:
        """
        全量重载所有配置文件

        Returns:
            变更摘要 {section: "updated" | "added" | "removed"}
        """
        old_config = self._load_all()
        changes = self._detect_changes(old_config, self._config)

        if changes:
            self._notify_callbacks(changes)

        if self._logger:
            self._logger.info(f"[AgentConfig] 全量重载完成，变更段: {list(changes.keys())}")

        return changes

    def reload_section(self, section: str) -> bool:
        """
        仅重载指定配置段

        Args:
            section: 配置段名，如 "llm"、"security"

        Returns:
            是否有变更
        """
        source_file = self._section_sources.get(section)
        if not source_file:
            if self._logger:
                self._logger.warning(f"[AgentConfig] 未知的配置段: {section}")
            return False

        filepath = self._config_dir / source_file
        if not filepath.exists():
            return False

        try:
            new_data = self._load_single_file(filepath)
            if new_data is None:
                return False

            with self._lock:
                old_value = self._config.get(section, {}).copy() if isinstance(self._config.get(section), dict) else self._config.get(section)
                self._config = self._deep_merge(self._config, new_data)
                new_value = self._config.get(section)
                self._mtimes[source_file] = filepath.stat().st_mtime

            self._notify_callbacks({section: ("updated", old_value, new_value)})
            if self._logger:
                self._logger.info(f"[AgentConfig] 配置段已热重载: {section}")
            return True

        except Exception as e:
            if self._logger:
                self._logger.error(f"[AgentConfig] 重载 {section} 失败: {e}")
            return False

    def reload_changed_files(self) -> Dict[str, str]:
        """
        检测并仅重载有变更的文件（高效增量重载）

        Returns:
            变更摘要
        """
        changed_sections = []
        for filename, last_mtime in list(self._mtimes.items()):
            filepath = self._config_dir / filename
            if filepath.exists():
                current_mtime = filepath.stat().st_mtime
                if current_mtime > last_mtime:
                    changed_sections.append(filename.replace(".yaml", ""))

        results = {}
        for section in changed_sections:
            success = self.reload_section(section)
            results[section] = "reloaded" if success else "failed"
        return results

    # ---- 自动重载 ----

    @property
    def auto_reload(self) -> bool:
        return self._auto_reload

    @auto_reload.setter
    def auto_reload(self, enabled: bool):
        self._auto_reload = enabled
        if self._logger:
            state = "开启" if enabled else "关闭"
            self._logger.info(f"[AgentConfig] 文件自动检测已{state}")

    def _check_auto_reload(self):
        """如果开启自动重载，每次读取前检测文件变更"""
        if not self._auto_reload:
            return
        self.reload_changed_files()

    # ---- 变更通知 ----

    def on_change(self, section: str, callback: ConfigChangeCallback):
        """
        注册配置变更回调

        Args:
            section: 要监听的配置段名（"*" = 监听所有段）
            callback: 回调函数 callback(old_value, new_value)
        """
        with self._lock:
            if section not in self._callbacks:
                self._callbacks[section] = []
            self._callbacks[section].append(callback)

    def off_change(self, section: str, callback: ConfigChangeCallback):
        """移除变更回调"""
        with self._lock:
            if section in self._callbacks:
                self._callbacks[section] = [c for c in self._callbacks[section] if c != callback]

    def _notify_callbacks(self, changes: Dict[str, tuple]):
        """触发变更回调"""
        for section, change_info in changes.items():
            action, old_val, new_val = change_info if isinstance(change_info, tuple) else ("updated", None, change_info)

            # 通知该段的专属回调
            for cb in self._callbacks.get(section, []):
                try:
                    cb(section, old_val, new_val)
                except Exception as e:
                    if self._logger:
                        self._logger.error(f"[AgentConfig] 回调异常 ({section}): {e}")

            # 通知全局 "*" 回调
            for cb in self._callbacks.get("*", []):
                try:
                    cb(section, old_val, new_val)
                except Exception as e:
                    if self._logger:
                        self._logger.error(f"[AgentConfig] 全局回调异常 ({section}): {e}")

    def _detect_changes(self, old: Dict, new: Dict) -> Dict[str, tuple]:
        """比较新旧配置，返回变更段"""
        changes = {}
        all_keys = set(list(old.keys()) + list(new.keys()))
        for key in all_keys:
            if key not in old:
                changes[key] = ("added", None, new.get(key))
            elif key not in new:
                changes[key] = ("removed", old.get(key), None)
            elif old[key] != new[key]:
                changes[key] = ("updated", old.get(key), new.get(key))
        return changes

    # ---- 工具方法 ----

    def list_files(self) -> Dict[str, Dict[str, Any]]:
        """列出所有已识别的配置文件及其状态"""
        result = {}
        for filename in self.DEFAULT_FILES:
            filepath = self._config_dir / filename
            info = {
                "exists": filepath.exists(),
                "sections": [],
            }
            if filepath.exists():
                info["mtime"] = filepath.stat().st_mtime
                info["size_kb"] = round(filepath.stat().st_size / 1024, 1)
                # 该文件贡献了哪些配置段
                info["sections"] = [
                    k for k, src in self._section_sources.items() if src == filename
                ]
            result[filename] = info
        return result

    def validate(self) -> Dict[str, List[str]]:
        """校验配置合法性，返回错误列表"""
        errors: Dict[str, List[str]] = {}
        provider = self.get("llm.provider", "")
        if provider not in ("openai", "ollama", "custom"):
            errors.setdefault("llm", []).append(f"无效的 provider: {provider}")
        perm_level = self.get("security.permission_level", "")
        if perm_level not in ("read_only", "standard", "admin", "unrestricted"):
            errors.setdefault("security", []).append(f"无效的 permission_level: {perm_level}")
        log_level = self.get("logging.level", "").upper()
        if log_level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
            errors.setdefault("logging", []).append(f"无效的 level: {log_level}")
        return errors

    @staticmethod
    def _get_logger():
        import logging
        return logging.getLogger(__name__)

    def __repr__(self) -> str:
        return (
            f"AgentConfig(dir={self._config_dir}, "
            f"sections={list(self._config.keys())}, "
            f"auto_reload={self._auto_reload})"
        )


# ============================================================
# 全局单例 & 便捷函数
# ============================================================

_global_config: Optional[AgentConfig] = None


def get_config(config_dir: Optional[pathlib.Path] = None) -> AgentConfig:
    """获取全局配置单例"""
    global _global_config
    if _global_config is None:
        _global_config = AgentConfig(config_dir)
    return _global_config


def reset_config():
    """重置单例（测试用）"""
    global _global_config
    if _global_config is not None:
        _global_config = None
