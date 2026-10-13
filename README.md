# 停车场管理智能体（parkade-agent）

课程：《人工智能创新开发实践》课程设计 —— 任务书场景 1：停车场管理智能体（多模态 + 工具调用 Agent）。

用户用自然语言对话，Agent 自主调用工具完成：抓拍识别车牌（双通道 OCR + 视觉模型 + 仲裁）、车位查询、入场登记、计费结算、预约；费率**解释**走知识库 RAG（附引用），费率**计算**走工具（防算术幻觉）。

## 快速开始（Python 3.11 + Node 18+）

### 方式一：Web 版（Vue 前端，推荐）

```bash
# 一次性准备
py -3.11 -m venv .venv
source .venv/Scripts/activate        # Git Bash；cmd 用 .venv\Scripts\activate.bat
pip install -r requirements.txt

cd web && npm install && npm run build && cd ..   # 前端构建（首次或前端有改动时）
cp .env.example .env                 # 填入三个模型的 key（见下）

# 每次启动（三个终端）
uvicorn mock_service.main:app --port 8000   # 终端1：Mock 停车场服务
uvicorn server.app:app --port 8100          # 终端2：Web 服务（含前端页面）
# 浏览器打开 http://localhost:8100
```

前端开发模式（改 Vue 代码热更新）：`cd web && npm run dev`（http://localhost:5173，`/api` 自动代理到 8100）。

### 方式二：Streamlit 版（旧前端，保留备用）

```bash
uvicorn mock_service.main:app --port 8000   # 终端1
streamlit run app/streamlit_app.py          # 终端2 → http://localhost:8501
```

`.env` 需按**三个独立配置块**填写（见 `.env.example`）：`LLM_*`（主脑，qwen-plus / deepseek-chat）、`VISION_*`（车牌视觉通道，qwen-vl-plus / glm-4v-flash）、`EMBEDDING_*`（知识库嵌入，text-embedding-v3）。三者分别配置、禁止混用，换任一个只改对应那一块；代码里统一经 `common/config.py` 读取。填好后运行 `python scripts/check_api.py` 逐块验证。

## 目录导览

| 目录 | 内容 | 负责人 |
|---|---|---|
| `common/` | `rates.yaml` 费率单一事实源 + `pricing.py` 计费引擎（唯一实现）+ `config.py` 三模型配置 | A |
| `mock_service/` | 停车场模拟服务（FastAPI）：相机抓拍/车位/出入场/预约/虚拟时钟 + 看板调试 | B |
| `kb/` | 知识库：语料、建库脚本、标注评测集、命中率评测 | C |
| `agent/` | LangGraph Agent、工具、双通道识别与仲裁、RAG 检索（含中间件） | A |
| `server/` | Web 服务：用户管理（SQLite）/聊天/上传识别/看板调试代理/静态托管 | A |
| `web/` | **Vue3 + Element Plus 前端**：用户切换、聊天（引用折叠）、上传/抓拍识别、车位看板调试 | A |
| `app/` | Streamlit 前端（旧版，保留备用） | A |
| `scripts/` | 冒烟/测试脚本、链路自检、端到端演示 | A/B |
| `docs/` | 接口契约、三人执行方案、任务板、AI 档案、例会纪要、报告 | 全组 |

## 文档入口（先读这两份再动手）

- **`docs/接口契约.md`** —— 三方对接唯一依据，字段与规则冻结，变更走流程；
- **`docs/成员A/B/C执行方案.md`** —— 各自的任务卡、验收命令、AI 提问模板、红线；
- `开发方案.md` —— 总体方案（目标、架构、5 周排期、测试方案、答辩脚本）。

## 验收指标（90–100 档锚点）

RAG Top3 命中率 ≥95%（60 条标注集）；回答 100% 基于私有知识（域外拒答）；多轮对话不丢上下文；对话链路响应 ≤2 秒（保底 ≤3 秒，实测）；识别链路（视觉 API）延迟如实报告；唯一计费实现，语料费率与 `rates.yaml` 逐项一致。

## 协作约定（fork → PR，B/C 无需仓库权限）

- B/C 在 GitHub 上 **Fork** 本仓库 → 克隆各自的 fork 开工（git 步骤见各自执行方案末尾），完成后在自己 fork 的网页上 **Open pull request** → 目标 `159zxq/parkade-agent` 的 `main`，由 A 审查合并；
- 分支：各自 fork 里用 `feature/mock`（B）、`feature/kb`（C）；A 直接在本仓库 `feature/agent`；
- 提交信息：`[模块] 任务号 一句话`，如 `[mock] B3 exit 接通计费`；
- 每天 commit + push + 记 AI 档案（`docs/ai档案/`），每周例会后更新 `docs/任务板.md`。
