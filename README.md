# Cellium Agent

![Logo](logo.png)

一个轻量级、模型无关的 AI Agent 框架，支持任意 OpenAI 兼容 API。

## 特性

- 模型无关（OpenAI/DeepSeek/Ollama/本地模型）
- 三层记忆（人格 + 会话 + FTS5 长期检索）
- 启发式决策引擎（自动检测循环、终止迭代、引导换方向）
- 组件热插拔（app/components/ 下文件 3 秒自动加载）
- 事件驱动架构
- 流式响应（SSE）
- Flash 模式（跳过记忆注入）

## 快速开始

```bash
pip install -r requirements.txt
python main.py
```

```bash
pip install -r requirements.txt
```

主要依赖：
- FastAPI + Uvicorn（Web 框架）
- PyYAML（配置解析）
- Jieba（中文分词）
- DrissionPage（无头浏览器，用于网页搜索和抓取）

### 2. 配置模型

编辑 `config/agent/llm.yaml` 文件，配置 API 密钥、服务地址和模型名称。

支持的 API 服务：
- OpenAI
- 阶跃星辰
- DeepSeek
- Ollama（本地）
- LM Studio（本地）
- vLLM（本地）

### 3. 启动服务

```bash
python main.py
```

启动后访问 http://localhost:8000 打开聊天界面，访问 http://localhost:8000/docs 查看 API 文档。

## 架构

项目采用三层架构：

- **前端层** — React + TypeScript，通过 Fetch API 和 SSE 与后端通信
- **服务层** — FastAPI 提供 REST API 和流式聊天接口
- **Agent 核心** — 事件驱动主循环，协调 LLM、工具和记忆系统

### 核心组件

| 组件 | 说明 |
|------|------|
| AgentLoop | 事件驱动主循环，协调 LLM、工具、记忆 |
| LLM Engine | 统一 LLM 接口，支持任意 OpenAI 兼容 API |
| ThreeLayerMemory | 三层记忆：人格 + 会话 + 长期检索 |
| Tools | Shell、File、Memory、Web Search/Fetch 等可扩展工具 |
| EventBus | 事件总线，组件间松耦合通信 |

### 组件系统

| 目录 | 说明 |
|------|------|
| components/ | 可插拔组件（Web Search、Web Fetch 等） |
| components/skills/ | Skill 技能模块 |

## 目录结构

| 目录 | 说明 |
|------|------|
| app/agent/ | Agent 核心（主循环、LLM、工具、记忆、heuristics） |
| app/components/ | 热插拔组件目录 |
| app/server/ | FastAPI 服务 |
| config/agent/ | 配置文件 |
| html/ | 前端构建输出 |
| ui/ | React 前端源码 |
| memory/ | 记忆存储 |

## License

Apache 2.0