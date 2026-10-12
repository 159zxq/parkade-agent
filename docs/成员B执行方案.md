# 成员B执行方案 —— Mock 停车场服务（AI 辅助开发手册）

> 你承包的模块：`mock_service/` 全部接口 + 相机图生成 + 虚拟时钟 + 冒烟脚本。
> 对接依据：`docs/接口契约.md`（下称「契约」）。**代码按契约写，不自行改字段。**
> 用 AI 没问题，记住唯一铁律：**AI 出代码 → 你跑验收命令 → 过了才算完成；没跑过的代码不许提交。**

---

## 0. 你的产出清单（这就是你的成绩）

| 产出 | 时间 | 用途 |
|---|---|---|
| `mock_service/` 全部接口可运行（B1–B6） | W1 末起 | Agent 靠它干活，全项目地基 |
| `scripts/smoke_mock.py` 全绿 + 接口测试记录表 | W2–W3 | 测试报告素材 |
| 部署文档 mock 章节 + PPT 2 页 | W4 | 交付物②，答辩你讲这块 |
| AI 档案逐日记录（写清哪段 AI 写的、你怎么改的） | 全程 | 交付物③ |

## 1. 开始前（Day1–3，A 陪你做完）

B 只需要 Python 环境跑 mock 服务，**不需要任何 API key**。按顺序执行（按 §8 先 fork 并克隆**你自己的 fork**）：

```bash
git checkout -b feature/mock

# 1) 建环境（Python 必须用 3.11；装了多个版本时用 py -3.11）
py -3.11 -m venv .venv
source .venv/Scripts/activate          # Git Bash；cmd 用 .venv\Scripts\activate.bat
pip install -r requirements.txt        # 或用 requirements.lock.txt 精确锁版

# 2) 启动 mock（此时是 A 写的 stub 假数据版；你的任务是把假数据换成真逻辑）
uvicorn mock_service.main:app --reload --port 8000

# 3) 另开一个终端验证
curl http://127.0.0.1:8000/lots/status
```

看到 `{"ok": true, ...}` 即环境 OK。装依赖报错把**完整报错**发群里（最常见：用了系统默认的 3.14 而不是 3.11）。

## 2. 工作方式：任务卡三步走

每个任务卡都是一个循环：

1. **让 AI 写**——用下面的提问模板（复制，改括号内容）；
2. **你跑验收**——按任务卡的验收命令跑，过了才算完；
3. **记 AI 档案**——10 分钟，写清提示词和你改了什么（§5）。

提问模板：

```
我是 Python 初学者，在做停车场 Mock 服务（FastAPI + uvicorn），项目根目录是 mock_service/。
请帮我实现【接口名】。
必须遵守的接口契约（照抄，不许改字段）：
【从 docs/接口契约.md 粘贴该接口的段落】
现有代码：
【粘贴 mock_service/state.py 与 main.py 的当前内容】
要求：
1) 只修改【文件名】，不要动其他文件；
2) 时间必须用 clock.now()，禁止 datetime.now()；
3) 不要新增依赖；不要写测试文件；
4) 输出修改后的完整文件内容，并单独列出你改了哪几行。
```

追加技巧（很管用）：
- 让 AI **先给思路和关键代码片段，你确认后再要完整文件**——一次生成整文件容易埋错；
- 报错时把**报错原文**整段贴回去（别描述"它说错了"），附上你运行的命令；
- 同一个问题连续两次没解决 → 换一个 AI 工具问，或按 §4 的 30 分钟规则找人。

## 3. 任务卡

### 任务卡 B1：`/lots/status`（约 1.5h，W1）

- 改：`mock_service/state.py`（车位数据结构与初始驻车，见契约 §1、§5）、`main.py`（路由）。
- 要点：free/occupied 要跟着 `/entry`、`/exit` 变化，不是写死的。
- 验收：
  ```bash
  curl http://127.0.0.1:8000/lots/status          # A 区 free=27
  curl -X POST http://127.0.0.1:8000/entry -H "Content-Type: application/json" \
       -d '{"plate":"京Z99999"}'                  # ok:true
  curl http://127.0.0.1:8000/lots/status          # A 区 free=26
  ```
- 常见坑：curl 忘带 `Content-Type: application/json` 会报 422；文件要存 UTF-8 否则中文乱码。

### 任务卡 B2：`/entry`（约 2h，W1）

- 规则照契约 §3.4：重复入场 400 `ALREADY_INSIDE`；无空位 400 `NO_FREE_SPOT`；票据号 `T-0001` 递增。
- 验收：
  ```bash
  curl -X POST http://127.0.0.1:8000/entry -H "Content-Type: application/json" -d '{"plate":"京A12345"}'
  # 再来一次同样的命令 → 400，error.code == ALREADY_INSIDE
  ```

### 任务卡 B3：`/exit` + 接通计费（约 2h，W1）

- **费率逻辑不许自己写、也不许让 AI 写。** 直接调用 A 写好的函数：
  ```python
  from common.pricing import calc_fee
  fee = calc_fee(vehicle_type, entry_time, exit_time)
  ```
- `dry_run=true` 时只报价、不改状态（Agent 用它报价）。
- 验收（抄进你的接口测试记录）：
  ```bash
  # 1) 设虚拟时间 2026-11-10T10:00:00 → 入场 京A12345
  curl -X POST http://127.0.0.1:8000/debug/time -H "Content-Type: application/json" -d '{"set":"2026-11-10T10:00:00"}'
  curl -X POST http://127.0.0.1:8000/entry -H "Content-Type: application/json" -d '{"plate":"京A12345"}'
  # 2) 时间 +120 分钟
  curl -X POST http://127.0.0.1:8000/debug/time -H "Content-Type: application/json" -d '{"offset_minutes":120}'
  # 3) dry_run 报价 → total=12.00，且状态不变（再查状态车还在）
  curl -X POST http://127.0.0.1:8000/exit -H "Content-Type: application/json" -d '{"plate":"京A12345","dry_run":true}'
  # 4) 真出场 → total=12.00，A 区 free 回补 1
  curl -X POST http://127.0.0.1:8000/exit -H "Content-Type: application/json" -d '{"plate":"京A12345"}'
  ```

### 任务卡 B4：虚拟时钟与 reset（约 2h，W1）——契约 §3.7

- `mock_service/clock.py` 参考实现（A 已审，可直接抄）：
  ```python
  from datetime import datetime, timedelta

  class VirtualClock:
      def __init__(self):
          self._offset = timedelta(0)
      def now(self) -> datetime:
          return datetime.now() + self._offset
      def add_offset(self, minutes: int):
          self._offset += timedelta(minutes=minutes)
      def set_time(self, iso: str):
          self._offset = datetime.fromisoformat(iso) - datetime.now()
      def reset(self):
          self._offset = timedelta(0)

  clock = VirtualClock()
  ```
- 铁律自查：`grep -rn "datetime.now()" mock_service/`——除 clock.py 以外不许有结果，有就让 AI 改成 `clock.now()`。
- 验收：注入 +120 后 `GET /debug/time` 里 `offset_minutes=120`；期间入场的 `entry_time` 是虚拟时间；`POST /debug/reset` 后全部复原。

### 任务卡 B5：相机图 capture + 三变体 + truth（约 3h，W2）

- 参考思路（让 AI 完善成完整 `camera.py`，你负责验收）：
  ```python
  # 640×480 灰底场景 → 中央粘贴 260×82 蓝底白字车牌（微软雅黑 msyhbd.ttc 52pt，
  #   白描边 3px，绘制代码照抄 scripts/check_lpr.py 的 synth_plate/synth_scene，
  #   该规格已实测 HyperLPR3 识别 3/3，不要自己改尺寸和字号）
  # → 右下角水印 "{camera_id} {虚拟时间}" → 存 JPEG 字节返回
  # variant=="blur" → 整图 img.filter(ImageFilter.GaussianBlur(6))
  # variant=="occluded" → 用灰块盖住车牌中间两位（如 "京A**345"）
  # 参考样例：docs/样例/plate_京A12345.png（B 画出来的图应与它同规格）
  ```
- 相机→车牌映射照契约 §2 写死；`/debug/truth/{camera_id}` 返回同表内容。
- 验收：浏览器打开
  `http://127.0.0.1:8000/camera/cam-1/capture`（清晰车牌）；
  `.../capture?variant=blur`（明显模糊）；
  `.../capture?variant=occluded`（中部被挡）；
  `/debug/truth/cam-1` 与图中车牌照一致。

### 任务卡 B6：`/reserve`（约 1.5h，W2）——契约 §3.6

- 规则：同车牌未过期预约冲突 → `RESERVE_CONFLICT`；`keep_until = reserve_time + 30min`。
- 验收：预约两次同一车牌 → 第二次 400；GET 时间看 `keep_until` 正确。

### 任务卡 B7：冒烟脚本 + 接口测试记录（W2–W3，约 3h）

- `scripts/smoke_mock.py`：把上面所有 curl 改写成 Python 脚本（用 `httpx`），逐个断言关键字段，最后打印 `ALL PASS (N cases)`。
  - 给 AI 的安全用法：「这是我跑的 8 条 curl 和对应期望输出，改写成 httpx 断言脚本，用 `assert`，末尾打印 ALL PASS」。
- **接口测试记录表**（并入测试报告，编号 TC-Mock-xx）：

  | 用例号 | 操作 | 预期 | 实际 | 通过 |
  |---|---|---|---|---|
  | TC-Mock-01 | 入场重复车牌 | 400 ALREADY_INSIDE | 400 ALREADY_INSIDE | ✓ |
  | … | | | | |

### 任务卡 B8：部署文档 mock 章节 + PPT 2 页（W4）

- 部署文档：写「mock 怎么启动、端口、依赖、调试端点（/debug/time、/debug/reset 怎么用）」——照实写你每天开服务的真实步骤。
- PPT 两页：①接口总览图（让 AI 把你的接口列表画成图，你核对）；②「虚拟时钟如何让计费演示成立」（讲：现场不可能等 2 小时，注入时间后计费照常算）。

## 4. 卡住怎么办（30 分钟规则）

1. 报错**原文**贴给 AI，附上运行的最后一条命令 + 你已试过的做法；
2. 30 分钟仍搞不定 → 群里发：命令 + 完整报错 + 已试过什么 + 你的怀疑；
3. 对契约本身有疑惑（字段含义、规则冲突）→ **问 A，不要自己猜着改**。

## 5. AI 档案（每天 10 分钟，交付物③的组成部分）

文件写到 `docs/ai档案/2026-10-XX.md`，模板：

```markdown
## 2026-10-13 成员B —— B1 /lots/status
- 任务：把 stub 假数据换成真实车位状态
- 提示词（原文）：「我是 Python 初学者……（粘贴）」
- AI 原始输出：见 snippets/2026-10-13_lots_ai.py
- 人工修正：AI 直接写死了 occupied=3，改成从 state 里算；时间取 datetime.now() 改成 clock.now()
- 验证：curl 返回 free=27 ✓；入场后 free=26 ✓
```

要点：**提示词原文 + AI 原始代码另存 + 你怎么改的 + 验证结果**，四样齐。

## 6. 红线（违反任何一条 = 返工）

1. 不改契约里的字段名/结构/规则（要改先群里提，A 批准）；
2. 不碰 `common/pricing.py`（费率唯一实现，A 维护）；
3. 不碰别人的目录（`agent/`、`kb/`）；
4. 密钥不写进代码、不提交进仓库（`.env` 已被 .gitignore 忽略）；
5. 不用 `git push --force`；AI 给的"删除文件"类指令一律不执行；
6. **AI 代码的 bug 算你的 bug**——没跑过验收不许 commit。

## 7. 你的检查点（A 会在这几天验收）

| 节点 | 你要交 | 不达标后果（提前告诉你） |
|---|---|---|
| Day 3 | 环境跑通、能调通 stub | A 远程带你配一次 |
| W1 末 | B1–B4 完成，冒烟初版全绿 | 缺的部分 A 补实现，你转做 B7/B8（评分列不变） |
| W2 末 | B5/B6 完成，全接口对齐契约，冒烟全绿 | 同上 |
| W3 末 | 接口测试记录 + 模块章节初稿 | A 和你一起补 |

## 8. git 习惯（fork → PR，无需仓库权限）

**第一次准备**（网页 + 终端各一步）：

1. 打开 https://github.com/159zxq/parkade-agent → 点右上角 **Fork** → 创建到你自己的账号；
2. 克隆**你自己的 fork**（同页绿色 Code 按钮里有地址，把 `<你的用户名>` 换成你的 GitHub 用户名）：

   ```bash
   git clone https://github.com/<你的用户名>/parkade-agent.git
   cd parkade-agent
   git remote add upstream https://github.com/159zxq/parkade-agent.git
   git checkout -b feature/mock
   ```

3. 开工前想拿最新代码：`git fetch upstream && git merge upstream/main`（有冲突拿不准就发给 A，别硬解）。

**日常**：

- 每天收工：`git add -A && git commit -m "[mock] B3 exit 接通计费"` → `git push -u origin feature/mock`（推到你自己的 fork）；
- 完成一批任务卡（如 B1–B4）→ 在你 fork 的网页点 **Contribute → Open pull request**，目标分支选 `159zxq/parkade-agent` 的 `main`，标题写 `[mock] W1：B1-B4`，描述里贴验收命令输出；
- A 在 GitHub 上审查合并，**你不需要**仓库权限，也**不要**直推上游；不用 `git push --force`。
