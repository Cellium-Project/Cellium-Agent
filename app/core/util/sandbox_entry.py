# -*- coding: utf-8 -*-
import importlib
import importlib.util
import inspect
import logging
import os
import queue
import sys
import threading
import time
import traceback
from pathlib import Path

logger = logging.getLogger(__name__)

SANDBOX_TIMEOUT = 120


def _sandbox_worker(input_queue, output_queue, module_path, class_name, init_args, project_root):
    """沙箱工作进程"""
    try:
        if os.path.isdir(module_path) and os.path.exists(os.path.join(module_path, "__init__.py")):
            pkg_name = os.path.basename(module_path)
            components_dir = os.path.dirname(module_path)
            if components_dir not in sys.path:
                sys.path.insert(0, components_dir)

            for key in list(sys.modules.keys()):
                if key == pkg_name or key.startswith(f"{pkg_name}."):
                    try:
                        del sys.modules[key]
                    except Exception:
                        pass

            pycache_dir = os.path.join(module_path, "__pycache__")
            if os.path.exists(pycache_dir):
                for cached_file in Path(pycache_dir).glob("*.pyc"):
                    try:
                        cached_file.unlink()
                    except Exception:
                        pass

            for sub_dir in Path(module_path).iterdir():
                if sub_dir.is_dir() and (sub_dir / "__pycache__").exists():
                    for cached_file in (sub_dir / "__pycache__").glob("*.pyc"):
                        try:
                            cached_file.unlink()
                        except Exception:
                            pass

            component_class = None
            for py_file in Path(module_path).rglob("*.py"):
                if py_file.name.startswith("_") and py_file.name != "__init__.py":
                    continue

                rel_path = py_file.relative_to(module_path)
                module_parts = list(rel_path.parts[:-1]) + [rel_path.stem]
                if module_parts[-1] == "__init__":
                    module_parts = module_parts[:-1]
                sub_module_name = f"{pkg_name}.{'.'.join(module_parts)}" if module_parts else pkg_name

                try:
                    sub_module = importlib.import_module(sub_module_name)
                    if hasattr(sub_module, class_name):
                        obj = getattr(sub_module, class_name)
                        if inspect.isclass(obj):
                            component_class = obj
                            break
                except Exception:
                    continue

            if component_class is None:
                raise AttributeError(f"类不存在: {class_name} 在包 {pkg_name}")
        else:
            try:
                cached = importlib.util.cache_from_source(module_path)
                if cached and os.path.exists(cached):
                    os.remove(cached)
                    logger.debug("[Sandbox] 已删除缓存: %s", cached)
            except Exception as e:
                logger.debug("[Sandbox] 删除缓存失败: %s", e)

            try:
                pycache_dir = Path(module_path).parent / "__pycache__"
                if pycache_dir.exists():
                    stem = Path(module_path).stem
                    for cached_file in pycache_dir.glob(f"{stem}*.pyc"):
                        try:
                            cached_file.unlink()
                            logger.debug("[Sandbox] 已删除缓存(glob): %s", cached_file)
                        except Exception:
                            pass
            except Exception as e:
                logger.debug("[Sandbox] glob清理缓存失败: %s", e)

            unique_module_name = f"sandbox_component_{class_name}_{os.path.getmtime(module_path)}"
            spec = importlib.util.spec_from_file_location(unique_module_name, module_path)
            module = importlib.util.module_from_spec(spec)

            spec.loader.exec_module(module)
            component_class = getattr(module, class_name)

        component = component_class(**init_args)

        try:
            import psutil
            p = psutil.Process(os.getpid())
            rss_mb = p.memory_info().rss / 1024 / 1024
            logger.info("[Sandbox] 子进程 PID=%d 内存: %.1f MB", os.getpid(), rss_mb)
        except Exception as e:
            logger.warning("[Sandbox] 无法测量内存: %s", e)

        output_queue.put({"status": "ok", "cell_name": getattr(component, "cell_name", "unknown")})

        if hasattr(component, "on_load"):
            is_background = hasattr(component, '_running')

            if is_background:
                try:
                    component.on_load()
                    logger.debug("[Sandbox] on_load completed (background component)")
                except Exception as e:
                    logger.warning("[Sandbox] on_load failed: %s", e)
            else:
                def run_on_load():
                    try:
                        component.on_load()
                    except Exception as e:
                        logger.warning("[Sandbox] on_load failed: %s", e)
                thread = threading.Thread(target=run_on_load, daemon=True)
                thread.start()
                logger.debug("[Sandbox] on_load started in background thread")

        HEARTBEAT_INTERVAL = 30
        BACKGROUND_TIMEOUT = 300
        last_heartbeat = time.time()

        from queue import Empty as QueueEmpty

        while True:
            try:
                has_background = False
                if hasattr(component, '_running'):
                    has_background = component._running

                if has_background:
                    timeout = BACKGROUND_TIMEOUT
                else:
                    timeout = SANDBOX_TIMEOUT

                request = input_queue.get(timeout=timeout)
                if request is None:
                    break

                action = request.get("action")

                if action == "execute":
                    command = request.get("command", "")
                    args = request.get("args", [])
                    kwargs = request.get("kwargs", {})
                    result = component.execute(command, *args, **kwargs)
                    output_queue.put({"status": "ok", "result": result})

                elif action == "get_commands":
                    commands = component.get_commands()
                    output_queue.put({"status": "ok", "commands": commands})

                elif action == "get_command_params":
                    params_map = component.get_command_params() if hasattr(component, 'get_command_params') else {}
                    output_queue.put({"status": "ok", "params_map": params_map})

                elif action == "get_commands_meta":
                    commands_meta = {}
                    commands = component.get_commands()
                    for cmd_name in commands.keys():
                        method_name = f"_cmd_{cmd_name}"
                        method = getattr(component, method_name, None)
                        if method:
                            meta = {
                                "doc": method.__doc__ or "",
                                "name": cmd_name,
                            }
                            try:
                                sig = inspect.signature(method)
                                params = [p for p in sig.parameters.keys() if p != "self"]
                                meta["params"] = params
                            except Exception:
                                meta["params"] = []
                            commands_meta[cmd_name] = meta
                    output_queue.put({"status": "ok", "commands_meta": commands_meta})

                elif action == "has_method":
                    method_name = request.get("method_name", "")
                    has_it = hasattr(component, method_name) and callable(getattr(component, method_name))
                    output_queue.put({"status": "ok", "has": has_it})

                elif action == "ping":
                    output_queue.put({"status": "pong"})

                elif action == "heartbeat":
                    last_heartbeat = time.time()
                    has_bg = False
                    if hasattr(component, '_running'):
                        has_bg = component._running
                    output_queue.put({"status": "ok", "has_background": has_bg})

                else:
                    output_queue.put({"status": "error", "error": f"Unknown action: {action}"})

            except QueueEmpty:
                continue
            except Exception as e:
                error_str = str(e) or f"{type(e).__name__} (no error message)"
                output_queue.put({
                    "status": "error",
                    "error": error_str,
                    "error_type": type(e).__name__,
                    "traceback": traceback.format_exc(),
                })

    except Exception as e:
        error_str = str(e) or f"{type(e).__name__} (no error message)"
        output_queue.put({
            "status": "error",
            "error": f"Failed to init component: {error_str}",
            "error_type": type(e).__name__,
            "traceback": traceback.format_exc(),
        })
