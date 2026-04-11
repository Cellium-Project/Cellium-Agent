# -*- coding: utf-8 -*-
"""
Cellium Skills 目录

Skills 是 Cellium Agent 的能力扩展包，与组件(Components)的区别：

  组件 (components/*.py):
    - 系统内置工具，随系统启动自动加载
    - 通过 component.generate() 创建
    - 注册到 ComponentToolRegistry，LLM 可直接调用
    - 示例：text_processor, component_builder, skill_installer

  Skills (components/skills/*.py):
    - 可安装/卸载的能力包（类似插件）
    - 通过 skill_installer 工具管理
    - 存放于独立目录，不影响组件扫描
    - 安装后自动注册为 LLM 工具
    - 可以从远程仓库获取或本地文件导入
    - 示例：git_helper, code_refactor, doc_generator

目录结构：
  components/
    ├── __init__.py
    ├── component_builder.py      # 组件生成器（系统）
    ├── skill_installer.py        # Skill 管理器（系统）
    ├── text_processor.py          # 组件示例（系统）
    └── skills/                   # ★ Skill 安装目录
        ├── __init__.py           # 本文件

"""
