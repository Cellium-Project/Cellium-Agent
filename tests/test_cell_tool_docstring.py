# -*- coding: utf-8 -*-
"""组件 docstring 参数解析：Google / NumPy / Epydoc + 续行 + 类型兜底"""
from app.core.interface.base_cell import BaseCell
from app.core.util.cell_tool_adapter import CellToolAdapter


def test_google_args_multiline_continuation():
    doc = """推送到设备

    Args:
        message (str): 要推送的内容，留空用 pending.json
            第二行说明，含 默认值: 10
        delay (int): 开机后延迟秒数

    Returns:
        dict: 结果
    """
    docs = CellToolAdapter._parse_param_docs(doc)
    assert set(docs) == {"message", "delay"}
    assert "第二行说明" in docs["message"]["desc"]
    assert "默认值: 10" in docs["message"]["desc"]
    assert docs["delay"]["desc"] == "开机后延迟秒数"


def test_section_headers_stop_parsing():
    doc = """Args:
        a (str): 甲

    Example:
        x: 不是参数

    Note:
        y: 也不是参数
    """
    docs = CellToolAdapter._parse_param_docs(doc)
    assert set(docs) == {"a"}


def test_numpy_style_parameters():
    doc = """说明

    Parameters
    ----------
    name : str
        名称说明
    count : int, optional
        数量
    """
    docs = CellToolAdapter._parse_param_docs(doc)
    assert docs["name"]["type"] == "str"
    assert docs["name"]["desc"] == "名称说明"
    assert docs["count"]["type"] == "int"
    assert docs["count"]["desc"] == "数量"


def test_epydoc_params():
    doc = """@param url: 目标地址
    @type url: str
    """
    docs = CellToolAdapter._parse_param_docs(doc)
    assert docs["url"]["desc"] == "目标地址"
    assert docs["url"]["type"] == "str"


def test_definition_uses_doc_type_when_unannotated():
    class _Cell(BaseCell):
        """推送组件"""

        @property
        def cell_name(self):
            return "push_demo"

        def _cmd_push(self, message, delay):
            """推送到设备

            Args:
                message (str): 要推送的内容
                delay (int): 延迟秒数
            """
            return {"ok": True}

    adapter = CellToolAdapter(_Cell(), use_sandbox=False)
    params = adapter.definition["function"]["parameters"]["properties"]
    assert params["message"]["type"] == "string"
    assert params["delay"]["type"] == "integer"
    assert params["message"]["description"] == "要推送的内容"


def test_annotation_wins_over_doc_type():
    class _Cell(BaseCell):
        """类型优先"""

        @property
        def cell_name(self):
            return "typed_demo"

        def _cmd_run(self, count: int):
            """执行

            Args:
                count (str): 文档里写错类型
            """
            return {"ok": True}

    adapter = CellToolAdapter(_Cell(), use_sandbox=False)
    params = adapter.definition["function"]["parameters"]["properties"]
    assert params["count"]["type"] == "integer"
