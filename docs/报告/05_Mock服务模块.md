# 第五章 停车场模拟服务（Mock Service）模块设计

> 模块：`mock_service/`

## 5.1 模块定位

停车场模拟服务（下简称 Mock 服务）是整个系统的**数据地基与外部世界替身**。真实停车场系统包含相机、道闸、计费终端等硬件与外网服务，在课程设计场景下无法直接接入，因此本模块以 FastAPI 实现一套**行为等价**的模拟服务：

- 对**上游 Agent**：它是一个"会看车牌、会管车位、会算钱"的停车场系统；
- 对**系统内部**：它是所有可变状态（车位、票据、预约、时间）的唯一权威来源。

Mock 服务与真实业务系统之间的差异仅在"数据来源"——真实系统来自硬件，Mock 来自内存状态机；对外接口行为完全一致，因此上游 Agent 的代码无需为模拟环境做任何特殊处理。

**模块边界**（红线）：

- `mock_service/` 模块；
- 计费引擎 `common/pricing.py` 是全项目**唯一**计费实现，Mock 服务的 `/exit` 必须调用它，**禁止在 mock 内重写任何费率逻辑**；
- 时间必须取自 `mock_service/clock.py` 的 `clock.now()`，全仓库禁止 `datetime.now()`。

## 5.2 模块结构

```
mock_service/
├── __init__.py     包声明
├── main.py         FastAPI 路由层：全部 10 个接口
├── state.py        状态层：车位/票据/预约可变状态 + 相机映射常量
├── clock.py        虚拟时钟：全局单例 clock
└── camera.py       相机图合成：640×480 车牌图 + 三变体
```

分层清晰：**路由层**（main）只做参数校验与协议适配，**状态层**（state）持有全部可变数据，**时钟**（clock）与**相机**（camera）为独立能力单元。这种划分让"虚拟时钟"和"相机图"可以单独替换、单独测试。

## 5.3 数据模型与状态机

### 5.3.1 静态常量

| 常量 | 内容 | 说明 |
|---|---|---|
| `AREAS` / `areas_cfg` | A 地面 30 位 / B 地下一层 60 位 / C 地下二层 60 位 | 区域与容量 |
| `CAMERA_TABLE` | cam-1~cam-4 → 固定车牌 | 保证演示与测试可复现 |
| `INITIAL_OCCUPIED` | 初始驻车 A×3 / B×8 / C×2 | reset 后的基线状态 |
| `CAMERA_VARIANTS` | normal / blur / occluded | 相机图变体 |

相机→车牌映射固定为：cam-1(京A12345)、cam-2(京B88888)、cam-3(沪C66666)、cam-4(京A12345)。**固定映射**是本设计的关键决策——它让现场演示、自动化测试、双通道识别对账三者共享同一套可复现输入。

### 5.3.2 可变状态（`state.py: State`）

```
State
├── occupied:     dict[plate -> area]        当前在场车辆
├── tickets:      dict[plate -> Ticket]      票据（含入场时间、车型）
├── reservations: dict[plate -> Reservation] 有效预约
├── _ticket_seq:  int                        票据号计数器
└── _reserve_seq: int                        预约号计数器
```

- **票据号** `T-%04d`、**预约号** `R-%04d` 全局递增（`T-0001`、`R-0001`）；
- `add_entry` 同时写入 `occupied` 与 `tickets`，`remove_entry` 同时清除——保证两表一致；
- `active_reservation(plate)` 查询时惰性清理**已过期预约**（`keep_until <= now` 即自动释放），
  因此无需后台定时任务，简化了实现；
- `reset()` 恢复定义好的初始驻车，并把时钟一并归零。

### 5.3.3 数据结构

```python
@dataclass
class Ticket:
    ticket_id: str; plate: str; area: str
    vehicle_type: str; entry_time: datetime

@dataclass
class Reservation:
    reserve_id: str; plate: str; area: str | None
    reserve_time: datetime; keep_until: datetime
    status: str = "active"
```

## 5.4 接口设计

Mock 服务共实现 12 个接口，统一响应约定如下：

| # | 方法 | 路径 | 用途 | 关键规则 |
|---|---|---|---|---|
| 1 | GET | `/lots/status` | 车位状态查询 | 支持 `?area=` 过滤，大小写容错 |
| 2 | POST | `/entry` | 入场登记 | 重复车牌 `ALREADY_INSIDE`；无空位 `NO_FREE_SPOT` |
| 3 | POST | `/exit` | 出场结算 | 调 `calc_fee`；`dry_run` 只报价不改状态 |
| 4 | POST | `/reserve` | 预约车位 | 冲突 `RESERVE_CONFLICT`；`keep_until = +30min` |
| 5 | GET | `/camera/{id}/capture` | 抓拍单帧 | `?variant=normal\|blur\|occluded`，返回 JPEG |
| 6 | GET | `/debug/truth/{id}` | 真值对账（仅测试） | 返回相机固定车牌 |
| 7 | GET | `/debug/time` | 查询虚拟时间 | 返回 virtual/real/offset |
| 8 | POST | `/debug/time` | 注入时间 | 支持 `set` 或 `offset_minutes` |
| 9 | POST | `/debug/time/reset` | 时钟归零 | — |
| 10 | POST | `/debug/reset` | 全状态复位 | 车位/票据/预约/时钟一并恢复 |

### 5.4.1 统一响应约定

- **成功**：HTTP 200 + `{"ok": true, ...}`；
- **业务错误**：HTTP 400 + `{"ok": false, "error": {"code": "...", "message": "..."}}`；
- **资源不存在**：HTTP 404 + 同结构。

错误码严格限定在下列 8 个之内，**不允许自造新码**：
`CAMERA_NOT_FOUND` / `PLATE_NOT_FOUND` / `ALREADY_INSIDE` / `NOT_INSIDE` /
`RESERVE_CONFLICT` / `INVALID_PARAM` / `NO_FREE_SPOT` / `FEE_UNPAID`。

统一错误结构由 `_err(status, code, message)` 辅助函数集中产出，避免各处手写 JSON。

### 5.4.2 关键接口逻辑

**入场 `/entry`** 的判定顺序（顺序影响错误码正确性）：

```
1. 规范化车牌（去空格、字母大写）
2. 已在场？        → 400 ALREADY_INSIDE
3. 相机 id 合法？  → 404 CAMERA_NOT_FOUND
4. 指定区域：合法？有空位？ → 400 INVALID_PARAM / NO_FREE_SPOT
   未指定区域：按 A→B→C 找第一个有空位的区，全满 → 400 NO_FREE_SPOT
5. 写入状态，返回票据
```

车型取自相机映射（有 `camera_id` 时）或默认小型车。

**出场 `/exit`** 的核心是"计费与状态变更分离"：

```python
fee = calc_fee(ticket.vehicle_type, ticket.entry_time, clock.now())  # 唯一计费实现
body = {...fee...}
if not req.dry_run:
    state.remove_entry(plate)   # dry_run 时不动状态
```

`dry_run=true` 仅报价、不改变任何状态，供 Agent 在"报价"场景使用——这是"解释 vs 计算"边界在 Mock 层的体现。

## 5.5 虚拟时钟机制（本模块的技术亮点）

### 5.5.1 问题

计费演示的固有矛盾：**现场不可能真的等 2 小时**。若按真实时间计费，验收用例"10:00 入场、12:00 出场收 12 元"就无法在现场演示。

### 5.5.2 设计

`clock.py` 维护一个**时间偏移量** `_offset`，所有业务时间 = 真实时间 + 偏移：

```python
class VirtualClock:
    def now(self):                      return datetime.now() + self._offset  # 业务时间
    def real_now(self):                 return datetime.now()                # 仅供展示
    def add_offset(self, minutes):      self._offset += timedelta(minutes=minutes)
    def set_time(self, iso):            self._offset = datetime.fromisoformat(iso) - datetime.now()
    def reset(self):                    self._offset = timedelta(0)
    def offset_minutes(self):           return self._offset.total_seconds() / 60
```

**两种注入方式**：

- `{"set": "2026-11-10T10:00:00"}` —— 把虚拟时间**绝对设置**到某时刻（演示用）；
- `{"offset_minutes": 120}` —— 相对**快进** 120 分钟（"过了两小时"用）。

### 5.5.3 时钟纪律（全项目铁律）

约定：**全仓库业务时间必须取自 `clock.now()`**，`datetime.now()` 只允许出现在 `clock.py` 内部。

自查命令：

```bash
grep -rn "datetime.now()" mock_service/ --include="*.py" | grep -v clock.py   # 应为空
```

这条纪律保证"时间注入"能影响**所有**时间相关行为（入场时间、计费时长、预约过期），而不会因某处漏用了真实时间导致演示穿帮。本项目通过该自查，源码中 `datetime.now()` 仅出现在 `clock.py`。

### 5.5.4 演示价值

注入 +120 分钟后：

1. 期间入场的票据 `entry_time` 自动是虚拟时间；
2. `/exit` 计算时长时 `clock.now()` 返回虚拟时间 → 时长正确为 120 分钟；
3. 计费结果 12.00 元，与"真的等了 2 小时"完全一致。

这正是答辩演示脚本第 5 步的关键——"北京时间 10:00 入场，注入 +2 小时，现在出场要 12 元"。

## 5.6 相机图合成（`camera.py`）

### 5.6.1 需求

抓拍识别是系统多模态能力的入口，需要**可复现、可分级**的输入图像：

- **可复现**：同一相机每次抓拍内容一致（仅水印时间变），便于测试与演示；
- **可分级**：能主动制造"清晰/模糊/遮挡"三种难度，用于验证双通道识别与仲裁矩阵。

### 5.6.2 实现

用 PIL 运行时合成，**不依赖任何真实图片素材**：

```
640×480 灰底场景 (150,150,148)
  ├── 中央：蓝底(18,84,163)白字车牌，260×82，白框宽 3，微软雅黑 Bold 52pt
  ├── 右下角：水印 "{camera_id} {虚拟时间}"
  └── 左上角：位置标签（监控感）
```

### 5.6.3 三种变体

| 变体 | 处理 | 演示的识别分支 |
|---|---|---|
| `normal` | 无处理 | 双通道一致（`both_agree`） |
| `blur` | 整图高斯模糊 σ=6 | 本地 OCR 失败、视觉模型兜底（`vlm_only`） |
| `occluded` | 灰块遮挡车牌中部两位 | 两通道均失败，提示重拍（`failed`） |

### 5.6.4 规格冻结（重要）

车牌绘图规格（**蓝底白字、260×82、52pt**）并非随意选定：项目初期对多组参数做了识别实测，
发现 220×70/44pt 时"京B88888"识别失败，而 **260×82/52pt 下 HyperLPR3 识别 3/3**
（置信度 0.958–0.981）。该规格因此**冻结为固定规格**，任何改动前必须重跑
`tests/check_lpr.py` 验证。参考样例见 `docs/样例/plate_*.png`。

这一"用实测数据冻结接口规格"的做法，避免了"图改了、识别率掉了"的返工，是本模块与识别模块协作的关键接口。

## 5.7 测试与验证

本模块采用**脚本化自动测试**，全部断言真实运行、输出存证：

| 测试 | 脚本 | 规模 | 结果 |
|---|---|---|---|
| 接口冒烟 | `tests/smoke_mock.py` | 63 项 | ALL PASS |
| 计费一致性 | `tests/test_pricing.py` | 9 项 | ALL PASS |
| 时钟纪律 | grep 自查 | — | 通过 |
| 相机图规格 | 尺寸/格式校验 | — | 通过 |

用例覆盖：正常流、错误码（7 个错误码全覆盖）、边界（免费内、跨夜间、跨天、封顶）、
状态一致性（余位增减、票据递增、dry_run 不改状态）。完整用例表见
`docs/mock接口测试记录.md`（TC-Mock-xx / TC-Price-xx）。

**为何坚持脚本化测试**：Mock 服务是全项目地基，任何字段或规则的偏差都会沿调用链放大。
把验收固化成可重复运行的脚本，使得每次改动后都能一键回归，也为测试报告提供了直接可引用的证据。

## 5.8 小结

Mock 服务以约 600 行 Python 实现了停车场系统的完整行为等价物，核心设计决策有三：

1. **接口先定**：接口字段与规则一经确定，禁止随意变更；
2. **唯一实现原则**：计费走 `common/pricing.py`，时间走 `clock.now()`，消除多实现带来的不一致风险；
3. **可复现优先**：固定相机映射、固定绘图规格、虚拟时钟注入，让演示与测试完全可控。

该模块为上游 Agent 的双通道识别、工具调用、多轮对话提供了稳定、可复现的数据基础。
