# -*- coding: utf-8 -*-
"""
Cellium Skills 目录

Skills 是 Cellium Agent 的能力扩展包，与组件(Components)的区别：

  组件 (components/*.py):
    - 系统内置工具，随系统启动自动加载
    - 通过 component.generate() 创建
    - 注册到 ComponentToolRegistry，LLM 可直接调用
    - 示例：text_processor, component_builder, skill_installer

  Skills (components/skills/<name>/SKILL.md):
    - 可安装/卸载的能力包（类似插件）
    - 通过 skill_installer 工具管理
    - 存放于独立目录，每个 Skill 是一个包含 SKILL.md 的子目录
    - LLM 通过 skill_manager 获取 Skill 列表和元信息
    - LLM 通过 file_tool 读取 SKILL.md 获取完整使用信息
    - 示例：git_helper, code_refactor, doc_generator

目录结构：
  components/
    ├── __init__.py
    ├── component_builder.py      # 组件生成器（系统）
    ├── skill_installer.py        # Skill 安装器（系统）
    ├── skill_manager.py          # Skill 管理器（系统）
    ├── text_processor.py          # 组件示例（系统）
    └── skills/                   # Skill 安装目录
        ├── __init__.py           # 本文件
        ├── _index.json           # 已安装 Skill 索引
        └── <skill_name>/         # 每个 Skill 一个子目录
            └── SKILL.md          # Skill 完整使用信息

使用流程：
  1. skill_installer.install("git_helper") 安装 Skill
  2. skill_manager.list() 获取所有 Skill 列表
  3. skill_manager.get_info(name="git_helper") 获取元信息
  4. file_tool.read(path="<skill_md_path>") 读取完整 SKILL.md
"""
