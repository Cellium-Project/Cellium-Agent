# -*- coding: utf-8 -*-
"""
MemoryTool — LLM 可直接调用的长期记忆工具
"""

import logging
from typing import Any, Dict, Optional

from app.agent.tools.base_tool import BaseTool

logger = logging.getLogger(__name__)


class MemoryTool(BaseTool):
    """长期记忆工具 — 统一走 ThreeLayerMemory 仓库接口。"""

    name = "memory"
    description = (
        "长期记忆管理工具。支持搜索、写入、更新、删除、遗忘、冲突合并、概览和 Gene 查看。"
        "当用户告诉你重要偏好、项目约定、已解决问题等，应使用 store/update 维护长期记忆。"
        "当任务失败时，使用 list_genes 查看已有 Gene，get_gene 查看具体 Gene 内容。"
    )

    def __init__(self, three_layer_memory=None):
        super().__init__()
        self.memory = three_layer_memory

    @property
    def tool_name(self) -> str:
        return "memory"

    @property
    def definition(self) -> Dict:
        return {
            "type": "function",
            "function": {
                "name": self.tool_name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "要执行的命令",
                            "enum": ["search", "store", "list", "update", "delete", "forget", "merge", "list_genes", "get_gene", "read_archive"],
                        },
                        "entry_id": {"type": "string", "description": "[read_archive] Archive entry ID，用于读取完整对话历史"},
                        "page": {"type": "integer", "description": "[read_archive] 页码，默认 1"},
                        "page_size": {"type": "integer", "description": "[read_archive] 每页字符数，默认 2000"},
                        "task_type": {"type": "string", "description": "[get_gene] Gene 任务类型"},
                        "query": {"type": "string", "description": "[search/forget] 搜索关键词或问题"},
                        "title": {"type": "string", "description": "[store/update] 记忆标题"},
                        "content": {"type": "string", "description": "[store/update] 记忆内容"},
                        "category": {
                            "type": "string",
                            "description": "记忆分类",
                            "enum": ["preference", "code", "troubleshooting", "command", "general", "user_info", "project"],
                        },
                        "schema_type": {
                            "type": "string",
                            "description": "结构化 schema 类型",
                            "enum": ["general", "profile", "project", "issue", "control_gene"],
                        },
                        "tags": {"type": "string", "description": "逗号分隔标签"},
                        "source": {"type": "string", "description": "[update/delete] 记忆来源 ID"},
                        "memory_key": {"type": "string", "description": "[store/update/delete/merge] 结构化记忆键"},
                        "metadata": {"type": "object", "description": "结构化附加信息，如 field/project_id/problem/resolution"},
                        "allow_sensitive": {"type": "boolean", "description": "是否允许存储敏感信息（默认 false）"},
                        "all_matches": {"type": "boolean", "description": "[forget] 是否遗忘所有命中结果"},
                    },
                    "required": ["command"],
                },
            },
        }

    def execute(self, command="", *args, **kwargs) -> Dict[str, Any]:
        if isinstance(command, dict):
            return super().execute(command)
        if isinstance(command, str) and command.strip():
            return super().execute({"command": command, **kwargs})
        return {"success": False, "error": "未提供有效的 command 参数"}

    # ================================================================
    # 子命令实现
    # ================================================================

    def _cmd_search(
        self,
        query: str,
        schema_type: Optional[str] = None,
        category: Optional[str] = None,
    ) -> dict:
        if not self._check_memory():
            return {"success": False, "error": "长期记忆系统未初始化"}
        if not query or not query.strip():
            return {"success": False, "error": "搜索关键词不能为空"}

        try:
            results = self.memory.search_memories(
                query.strip(),
                top_k=5,
                category=category,
                schema_type=schema_type,
            )
            if not results:
                return {"success": True, "found": 0, "message": f"未找到与「{query}」相关的记忆", "results": []}

            items = []
            for item in results:
                metadata = item.get("metadata", {})
                result_item = {
                    "id": item.get("id"),
                    "title": item.get("title", ""),
                    "score": round(float(item.get("score", 0)), 4),
                    "category": item.get("category", "?"),
                    "note_type": item.get("note_type", ""),
                    "schema_type": item.get("schema_type", "general"),
                    "memory_key": item.get("memory_key", ""),
                    "tags": item.get("tags", ""),
                    "content": item.get("content", "")[:500],
                    "source": item.get("source_file", ""),
                }

                if metadata.get("memory_type") == "user_question":
                    entry_id = metadata.get("archive_entry_id")
                    if entry_id:
                        result_item["archive_entry_id"] = entry_id
                        result_item["hint"] = f"使用 memory.read_archive(entry_id='{entry_id}') 查看完整对话"

                items.append(result_item)

            logger.info("[MemoryTool] search | query=%s | found=%d", query[:50], len(items))
            return {"success": True, "found": len(items), "query": query, "results": items}
        except Exception as e:
            logger.error("[MemoryTool] search 失败 | error=%s", e)
            return {"success": False, "error": f"搜索失败: {e}"}

    def _cmd_store(
        self,
        title: str,
        content: str,
        category: str = "general",
        tags: str = "",
        schema_type: str = "general",
        memory_key: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        allow_sensitive: bool = False,
    ) -> dict:
        if not self._check_memory():
            return {"success": False, "error": "长期记忆系统未初始化"}
        if not title or not title.strip():
            return {"success": False, "error": "标题不能为空"}
        if not content or not content.strip():
            return {"success": False, "error": "内容不能为空"}

        try:
            result = self.memory.upsert_memory(
                title=title.strip(),
                content=content.strip(),
                category=category or "general",
                tags=tags or "",
                schema_type=schema_type or "general",
                memory_key=memory_key or "",
                metadata=metadata or {},
                allow_sensitive=allow_sensitive,
                merge_strategy="merge",
            )
            if not result.get("success"):
                return {"success": False, "error": result.get("error", "写入失败")}
            logger.info("[MemoryTool] store | title=%s | action=%s", title[:40], result.get("action"))
            return {
                "success": True,
                "action": result.get("action"),
                "id": result.get("id"),
                "source": result.get("source"),
                "message": f"已记住: {title}",
                "sensitive": result.get("sensitive", False),
            }
        except Exception as e:
            logger.error("[MemoryTool] store 失败 | error=%s", e)
            return {"success": False, "error": f"写入失败: {e}"}

    def _cmd_update(
        self,
        source: Optional[str] = None,
        memory_key: Optional[str] = None,
        title: Optional[str] = None,
        content: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[str] = None,
        schema_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        allow_sensitive: bool = False,
    ) -> dict:
        if not self._check_memory():
            return {"success": False, "error": "长期记忆系统未初始化"}
        if not source and not memory_key:
            return {"success": False, "error": "update 需要 source 或 memory_key"}

        try:
            result = self.memory.update_memory(
                source=source,
                memory_key=memory_key,
                title=title,
                content=content,
                category=category,
                tags=tags,
                schema_type=schema_type,
                metadata=metadata or {},
                allow_sensitive=allow_sensitive,
            )
            if not result.get("success"):
                return {"success": False, "error": result.get("error", "更新失败")}
            return {"success": True, "id": result.get("id"), "source": result.get("source"), "message": "记忆已更新"}
        except Exception as e:
            logger.error("[MemoryTool] update 失败 | error=%s", e)
            return {"success": False, "error": f"更新失败: {e}"}

    def _cmd_delete(self, source: Optional[str] = None, memory_key: Optional[str] = None) -> dict:
        if not self._check_memory():
            return {"success": False, "error": "长期记忆系统未初始化"}
        if not source and not memory_key:
            return {"success": False, "error": "delete 需要 source 或 memory_key"}

        try:
            result = self.memory.delete_memory(source=source, memory_key=memory_key)
            if not result.get("success"):
                return {"success": False, "error": result.get("error", "删除失败")}
            return {"success": True, "id": result.get("id"), "message": "记忆已删除"}
        except Exception as e:
            logger.error("[MemoryTool] delete 失败 | error=%s", e)
            return {"success": False, "error": f"删除失败: {e}"}

    def _cmd_forget(self, query: Optional[str] = None, source: Optional[str] = None, all_matches: bool = False) -> dict:
        if not self._check_memory():
            return {"success": False, "error": "长期记忆系统未初始化"}
        if not query and not source:
            return {"success": False, "error": "forget 需要 query 或 source"}

        try:
            result = self.memory.forget_memories(query=query, source=source, all_matches=all_matches)
            if not result.get("success"):
                return {"success": False, "error": result.get("error", "遗忘失败")}
            return {"success": True, "forgotten": result.get("forgotten", []), "message": "记忆已遗忘"}
        except Exception as e:
            logger.error("[MemoryTool] forget 失败 | error=%s", e)
            return {"success": False, "error": f"遗忘失败: {e}"}

    def _cmd_merge(self, memory_key: Optional[str] = None, schema_type: Optional[str] = None) -> dict:
        if not self._check_memory():
            return {"success": False, "error": "长期记忆系统未初始化"}
        if not memory_key and not schema_type:
            return {"success": False, "error": "merge 需要至少提供 memory_key 或 schema_type"}

        try:
            result = self.memory.merge_conflicts(memory_key=memory_key, schema_type=schema_type)
            return {"success": True, **result, "message": "冲突合并完成"}
        except Exception as e:
            logger.error("[MemoryTool] merge 失败 | error=%s", e)
            return {"success": False, "error": f"合并失败: {e}"}

    def _cmd_list(self, schema_type: Optional[str] = None, category: Optional[str] = None) -> dict:
        if not self._check_memory():
            return {"success": False, "error": "长期记忆系统未初始化"}

        try:
            raw = self.memory.list_memories(schema_type=schema_type, category=category, limit=50)
            items = raw.get("items", []) if isinstance(raw, dict) else raw
            categories = {}
            schemas = {}
            for item in items:
                categories[item.get("category", "general")] = categories.get(item.get("category", "general"), 0) + 1
                schemas[item.get("schema_type", "general")] = schemas.get(item.get("schema_type", "general"), 0) + 1
            return {
                "success": True,
                "total_memories": len(items),
                "categories": [{"category": key, "count": value} for key, value in sorted(categories.items())],
                "schemas": [{"schema_type": key, "count": value} for key, value in sorted(schemas.items())],
                "items": [
                    {
                        "id": item.get("id"),
                        "title": item.get("title"),
                        "category": item.get("category"),
                        "schema_type": item.get("schema_type"),
                        "memory_key": item.get("memory_key"),
                        "source": item.get("source_file"),
                    }
                    for item in items[:20]
                ],
                "message": f"共 {len(items)} 条活跃记忆",
            }
        except Exception as e:
            logger.error("[MemoryTool] list 失败 | error=%s", e)
            return {"success": False, "error": f"查询失败: {e}"}

    def _cmd_list_genes(self) -> dict:
        if not self._check_memory():
            return {"success": False, "error": "长期记忆系统未初始化"}
        
        try:
            results = self.memory.list_memories(
                schema_type="control_gene",
                limit=50,
            )
            items = results.get("items", [])
            
            genes = []
            for item in items:
                metadata = item.get("metadata", {})
                genes.append({
                    "task_type": metadata.get("task_type", ""),
                    "version": metadata.get("version", 1),
                    "success_rate": round(metadata.get("success_rate", 0.0), 2),
                    "usage_count": metadata.get("usage_count", 0),
                    "signals": metadata.get("signals", []),
                })
            
            return {
                "success": True,
                "total_genes": len(genes),
                "genes": genes,
                "message": f"找到 {len(genes)} 个 Gene，使用 get_gene 命令查看详情",
            }
        except Exception as e:
            logger.error("[MemoryTool] list_genes 失败 | error=%s", e)
            return {"success": False, "error": f"查询失败: {e}"}

    def _cmd_get_gene(self, task_type: str) -> dict:
        if not self._check_memory():
            return {"success": False, "error": "长期记忆系统未初始化"}
        if not task_type:
            return {"success": False, "error": "task_type 不能为空"}
        
        try:
            memory_key = f"gene:{task_type}"
            
            results = self.memory.repository.search_memories(
                query=task_type,  
                schema_type="control_gene",
                top_k=10 
            )
            
            result = None
            for r in results:
                if r.get("memory_key") == memory_key:
                    result = r
                    break
            
            if not result:
                logger.debug("[MemoryTool] get_gene 方法1未找到，尝试方法2 | task_type=%s", task_type)
                all_results = self.memory.repository.search_memories(
                    query="gene",
                    schema_type="control_gene",
                    top_k=100
                )
                for r in all_results:
                    if r.get("memory_key") == memory_key:
                        result = r
                        break
            
            if not result:
                return {"success": False, "error": f"未找到 Gene: {task_type}"}
            
            metadata = result.get("metadata", {})
            return {
                "success": True,
                "task_type": metadata.get("task_type", task_type),
                "content": result.get("content", ""),
                "version": metadata.get("version", 1),
                "success_rate": round(metadata.get("success_rate", 0.0), 2),
                "usage_count": metadata.get("usage_count", 0),
                "forbidden_tools": metadata.get("forbidden_tools", []),
                "preferred_tools": metadata.get("preferred_tools", []),
                "evolution_history": metadata.get("evolution_history", [])[-5:],
            }
        except Exception as e:
            logger.error("[MemoryTool] get_gene 失败 | error=%s", e)
            return {"success": False, "error": f"查询失败: {e}"}

    def _cmd_read_archive(self, entry_id: str, page: int = 1, page_size: int = 2000) -> dict:
        """读取 Archive 中的最终助手回复（支持分页）"""
        if not self._check_memory():
            return {"success": False, "error": "长期记忆系统未初始化"}
        if not entry_id:
            return {"success": False, "error": "需要提供 entry_id"}

        page = max(1, page)
        page_size = max(500, min(page_size, 8000)) 

        try:
            record = self.memory.archive.get_by_id(entry_id)
            if not record:
                return {"success": False, "error": f"未找到 Archive entry: {entry_id}"}

            messages = record.get("messages", [])

            assistant_response = None
            for msg in reversed(messages):
                if msg.get("role") == "assistant":
                    assistant_response = msg.get("content", "")
                    break

            if not assistant_response:
                return {"success": False, "error": "未找到助手回复"}

            total_chars = len(assistant_response)
            total_pages = (total_chars + page_size - 1) // page_size

            start_idx = (page - 1) * page_size
            end_idx = min(start_idx + page_size, total_chars)

            if start_idx >= total_chars:
                return {
                    "success": True,
                    "entry_id": entry_id,
                    "page": page,
                    "page_size": page_size,
                    "total_pages": total_pages,
                    "total_chars": total_chars,
                    "answer": "",
                    "has_more": False,
                    "message": f"页码超出范围 (共 {total_pages} 页)",
                }

            paginated_answer = assistant_response[start_idx:end_idx]
            has_more = end_idx < total_chars

            result = {
                "success": True,
                "entry_id": entry_id,
                "session_id": record.get("session_id"),
                "time": record.get("time"),
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "total_chars": total_chars,
                "answer": paginated_answer,
                "has_more": has_more,
            }

            if has_more:
                result["next_page_hint"] = f"使用 memory.read_archive(entry_id='{entry_id}', page={page + 1}) 查看下一页"

            return result
        except Exception as e:
            logger.error("[MemoryTool] read_archive 失败 | error=%s", e)
            return {"success": False, "error": f"读取失败: {e}"}

    # ================================================================
    # 内部方法
    # ================================================================

    def _check_memory(self) -> bool:
        if self.memory is None:
            logger.warning("[MemoryTool] three_layer_memory 未注入")
            return False
        return True
