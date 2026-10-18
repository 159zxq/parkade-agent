# Mock 服务接口测试记录（TC-Mock）

> 模块：`mock_service/`｜执行脚本：`tests/smoke_mock.py`、`tests/test_pricing.py`
> 覆盖：全部接口 + 计费一致性 + 时钟纪律
>
> 执行方式（项目根目录）：
>
> ```bash
> .venv/Scripts/python.exe tests/smoke_mock.py      # 接口冒烟 63 项（v1.3 起测试隔离：写 data/mock_smoke.sqlite）
> .venv/Scripts/python.exe tests/test_pricing.py    # 计费自测 9 项
> ```
>
> 最近执行：**2026-10-14** → 接口 **ALL PASS（共 63 项）**、计费 **ALL PASS（共 9 项）**。
> 下表「实际」栏均为脚本真实输出。

---

## 1. 接口用例表（TC-Mock-01 ~ 33）

### 1.1 B1 车位状态 `/lots/status`

| 用例号 | 操作 | 预期 | 实际 | 通过 |
|---|---|---|---|---|
| TC-Mock-01 | `GET /lots/status` | 返回 A/B/C 三区 | A/B/C | ✓ |
| TC-Mock-01b | 初始 A 区余位 | free=27（30−3） | 27 | ✓ |
| TC-Mock-01c | 初始 B 区余位 | free=52（60−8） | 52 | ✓ |
| TC-Mock-01d | 初始 C 区余位 | free=58（60−2） | 58 | ✓ |
| TC-Mock-01e | `GET /lots/status?area=a`（小写） | 仅返回 A 区（大小写容错） | `["A"]` | ✓ |
| TC-Mock-01f | `GET /lots/status?area=Z` | 400 `INVALID_PARAM` | 400 `INVALID_PARAM` | ✓ |
| TC-Mock-02c | 入场后再查 `?area=A` | free=26（余位 −1） | 26 | ✓ |

### 1.2 B2 入场 `/entry`

| 用例号 | 操作 | 预期 | 实际 | 通过 |
|---|---|---|---|---|
| TC-Mock-02 | `POST /entry {"plate":"京Z99999"}` | `ok:true`，area=A | 一致 | ✓ |
| TC-Mock-02b | 票据号 | `T-0001`（递增） | `T-0001` | ✓ |
| TC-Mock-03 | 重复入场同一车牌 | 400 `ALREADY_INSIDE` | 400 `ALREADY_INSIDE` | ✓ |
| TC-Mock-05 | 注入虚拟时间后入场 | `entry_time` 取虚拟时间 | `2026-11-10T10:00:00` | ✓ |

### 1.3 B3 出场计费 `/exit`

| 用例号 | 操作 | 预期 | 实际 | 通过 |
|---|---|---|---|---|
| TC-Mock-07 | `POST /exit {"dry_run":true}`（2 小时） | total=12.00 | 12.0 | ✓ |
| TC-Mock-07b | 计费明细 | 105 分 / 4 单位 | 105 / 4 | ✓ |
| TC-Mock-07c | dry_run 后查车位 | 车仍在场（不改状态） | free 不变 | ✓ |
| TC-Mock-08 | `POST /exit`（真出场） | total=12.00 | 12.0 | ✓ |
| TC-Mock-08b | 出场后查 `?area=A` | free 回补 | 26 | ✓ |
| TC-Mock-09 | 对不在场车牌出场 | 400 `NOT_INSIDE` | 400 `NOT_INSIDE` | ✓ |
| TC-Mock-20 | 跨夜间时段（21:30→23:10） | total=7.50 | 7.5 | ✓ |

### 1.4 B4 虚拟时钟与 reset `/debug/time`

| 用例号 | 操作 | 预期 | 实际 | 通过 |
|---|---|---|---|---|
| TC-Mock-04 | `POST /debug/time {"set":"2026-11-10T10:00:00"}` | virtual_time 为该值 | 一致 | ✓ |
| TC-Mock-06 | `POST /debug/time {"offset_minutes":120}` | virtual_time=12:00:00 | 一致 | ✓ |
| TC-Mock-15 | `POST /debug/time/reset` | offset_minutes=0 | 0 | ✓ |
| TC-Mock-16 | 注入 +90 后 `GET /debug/time` | offset_minutes=90 | 90 | ✓ |
| TC-Mock-17 | `POST /debug/reset` 后查车位 | 恢复 A.free=27 | 27 | ✓ |
| TC-Mock-00 | `POST /debug/reset` | `ok:true`，状态归位 | `ok:true` | ✓ |

### 1.5 B5 相机图 `/camera/{id}/capture` + `/debug/truth`

| 用例号 | 操作 | 预期 | 实际 | 通过 |
|---|---|---|---|---|
| TC-Mock-13 | `capture?variant=normal` | image/jpeg，>1KB | 640×480 JPEG | ✓ |
| TC-Mock-13 | `capture?variant=blur` | image/jpeg，>1KB | 640×480 JPEG | ✓ |
| TC-Mock-13 | `capture?variant=occluded` | image/jpeg，>1KB | 640×480 JPEG | ✓ |
| TC-Mock-12 | `GET /debug/truth/cam-1` | plate=京A12345，小型车 | 一致 | ✓ |
| TC-Mock-14 | `GET /camera/cam-9/capture`（未知相机） | 404 `CAMERA_NOT_FOUND` | 404 `CAMERA_NOT_FOUND` | ✓ |
| TC-Mock-14b | `capture?variant=xxx`（非法变体） | 400 `INVALID_PARAM` | 400 `INVALID_PARAM` | ✓ |

### 1.6 B6 预约 `/reserve`

| 用例号 | 操作 | 预期 | 实际 | 通过 |
|---|---|---|---|---|
| TC-Mock-10 | `POST /reserve` @18:00 | keep_until=18:30 | `2026-11-10T18:30:00` | ✓ |
| TC-Mock-10b | 预约号 | `R-0001`（递增） | `R-0001` | ✓ |
| TC-Mock-11 | 同车牌重复预约 | 400 `RESERVE_CONFLICT` | 400 `RESERVE_CONFLICT` | ✓ |

### 1.7 v1.1/v1.2 新增接口（看板调试 + 单车查询，2026-10-14 补测）

| 用例号 | 操作 | 预期 | 实际 | 通过 |
|---|---|---|---|---|
| TC-Mock-30 | `GET /vehicle/京A12345`（未入场） | `inside:false` | 一致 | ✓ |
| TC-Mock-31 | 入场 +90 分钟后 `GET /vehicle` | `inside:true`、duration=90、entry=虚拟时间 | 一致 | ✓ |
| TC-Mock-32 | `GET /vehicle/abc`（任意车牌） | 200、`inside:false`（不 404） | 一致 | ✓ |
| TC-Mock-33 | `POST /debug/lots` 改总车位（30→50） | total=50 | 50 | ✓ |
| TC-Mock-34 | `POST /debug/lots` 改占用（→45） | occupied=45、free=5 | 一致 | ✓ |
| TC-Mock-35 | `POST /debug/lots` 超容量 | 400 `INVALID_PARAM` | 一致 | ✓ |
| TC-Mock-36 | `/debug/reset` 恢复容量与占用 | total=30、free=27 | 一致 | ✓ |

### 1.8 v1.3 新增：记录库 + 预约占位 + 重启恢复（2026-10-14 补测）

需求方迭代：「要有一个数据库记录进入时间，离开时间，缴费状态（离开默认已缴费）预约情况，
不然就算进场了也说成没有进场；车位看板在车辆进场时和被预约占用也不会有变化。」

| 用例号 | 操作 | 预期 | 实际 | 通过 |
|---|---|---|---|---|
| TC-Mock-40 | `GET /lots/status` | 含 `reserved` 字段（初始 0） | 一致 | ✓ |
| TC-Mock-41 | 预约 B 区后查看板 | `reserved=1`、`free` −1（60−8−1=51） | 一致 | ✓ |
| TC-Mock-42 | 预约车 `POST /entry`（不给 area） | 自动进 B 区（预约区）、`reservation_consumed=true` | 一致 | ✓ |
| TC-Mock-42b | 核销后查看板 | `reserved=0`、`occupied=9`、`free` 仍 51 | 一致 | ✓ |
| TC-Mock-43 | `GET /records?plate=京B88888` | 票据含进入时间；出场前 `exit_time=null`、`status=active` | 一致 | ✓ |
| TC-Mock-43b | 同上 | 预约 `status=consumed`（已核销） | 一致 | ✓ |
| TC-Mock-44 | `POST /exit` | 返回 `paid=true`（离开默认已缴费） | 一致 | ✓ |
| TC-Mock-44b | 出场后查 `/records` | 离开时间/费用/`paid=1`/`status=closed` 均已落库 | 一致 | ✓ |
| TC-Mock-45 | 出场后 `GET /vehicle` | `last_visit` 含离开时间/时长/费用/缴费状态 | 一致 | ✓ |
| TC-Mock-46 | 模拟重启（重建 State + `restore_from_db`）后 `GET /vehicle` | 在场车仍在场（票号/区域一致）——**根治"进场了却说没进场"** | 一致 | ✓ |
| TC-Mock-46b | 恢复后查看板 | 占用计数一致（A occupied=4） | 一致 | ✓ |
| TC-Mock-47 | `/debug/reset` | 记录库清空（tickets=[]、reservations=[]） | 一致 | ✓ |

**接口用例合计 52 项，全部通过（`tests/smoke_mock.py` 复跑）。**
浏览器端实测（截图 `docs/测试证据/vue-v13-*.png`）：对话让车入场 → 看板 5 秒内自动刷新（27→26）；
预约 → 看板即时显示"预约保留 1"；"记录"弹窗展示进入/离开时间、4.50 元、已缴费、预约状态；
重启 mock 服务后 `GET /vehicle` 仍在场；用户问"刚才那笔停车费交了吗"→ Agent 答"已缴费 ✅、
91 分钟、4.50 元"并主动更正不在场状态。

### 1.9 v1.5 缴费 POST /pay（2026-10-15 定稿：现缴 + 月卡）

需求方迭代：「不需要冲抵，不需要预缴，只要现缴和月卡。」（v1.4 预缴/冲抵模型已废弃移除）

| 用例号 | 操作 | 预期 | 实际 | 通过 |
|---|---|---|---|---|
| TC-Mock-50 | 不在场现缴 `POST /pay` | 400 `NOT_INSIDE`（无待缴费用） | 一致 | ✓ |
| TC-Mock-51 | 非法 type | 400 `INVALID_PARAM` | 一致 | ✓ |
| TC-Mock-52 | 在场现缴（120 分钟） | 按出场费 12.00 当场结清、`type=spot`、P-0001 | 一致 | ✓ |
| TC-Mock-52b | `GET /records` | 缴费记录 `type=spot`，无预缴字段 | 一致 | ✓ |
| TC-Mock-53 | 月卡办理（不在场，小型车） | 300.00 元、`valid_until` 30 天后 | 一致 | ✓ |
| TC-Mock-53b | 重复办月卡 | 400 `INVALID_PARAM` | 一致 | ✓ |
| TC-Mock-53c | `GET /vehicle` | `monthly` 生效中 | 一致 | ✓ |
| TC-Mock-54 | 月卡车出场 | `total=0`、`monthly=true`（免计费） | 一致 | ✓ |
| TC-Mock-55 | 模拟重启（restore_from_db） | 有效月卡恢复（仍生效） | 一致 | ✓ |
| TC-Mock-55b | 恢复集 | 仅含有效月卡（现缴无恢复语义） | 一致 | ✓ |
| TC-Mock-56 | `/debug/reset` | 缴费记录清空 | 一致 | ✓ |

**接口用例合计 63 项，全部通过。** 浏览器端实测（截图 `docs/测试证据/vue-pay-spot-monthly.png`、
`vue-v15-pay-records.png`、`vue-v15-agent-monthly-qa.png`）：试算 ¥12.00 → 现缴成功（P-0001）→
切月卡办理成功（P-0002 · ¥300.00 · 有效至 30 天后）；记录弹窗"缴费（现缴/月卡）"表分类展示；
问 Agent"我的车停在哪个区域？我有月卡吗？"→ 答"停在 A 区、无生效中的月卡"（主动更正），
追问费用 → 工具计算 15.0 元并说明已缴费。

---

## 2. 计费一致性用例（TC-Price-xx，`common/pricing.py` 唯一实现）

执行 `python tests/test_pricing.py` → **ALL PASS（共 9 项）**。

| 用例号 | 场景 | 期望 | 实际 | 通过 |
|---|---|---|---|---|
| TC-Price-01 | 小型车 10:00→12:00（样例1） | 12.00 元 / 4 单位 | 12.0 / 4 | ✓ |
| TC-Price-02 | 小型车 21:30→23:10（跨夜间，样例2） | 7.50 元 / 4 单位 | 7.5 / 4 | ✓ |
| TC-Price-03 | 小型车 11-10 21:00→11-11 01:00（跨天，样例3） | 15.00 元 / 8 单位 | 15.0 / 8 | ✓ |
| TC-Price-04 | 小型车 07:00→23:00（触当日封顶） | 30.00 元，cap_applied | 30.0 / True | ✓ |
| TC-Price-05 | 小型车 10:00→10:15（免费内） | 0.00 元 | 0.0 | ✓ |
| TC-Price-06 | 小型车 10:00→10:16（超 15 分钟） | 3.00 元 / 1 单位 | 3.0 / 1 | ✓ |
| TC-Price-07 | 中型车 10:00→12:00 | 20.00 元 / 4 单位 | 20.0 / 4 | ✓ |
| TC-Price-08 | 月卡车 | 0.00 元 | 0.0 | ✓ |
| TC-Price-09 | 未知车型 | 抛 ValueError | ValueError | ✓ |

---

## 3. 时钟纪律自查

```bash
grep -rn "datetime.now()" mock_service/ --include="*.py" | grep -v clock.py
```

结果：**输出为空** —— 源码中 `datetime.now()` 仅出现在 `mock_service/clock.py`（时钟实现本体），
其余业务代码一律使用 `clock.now()`，符合虚拟时钟纪律。

> 注：若不加 `--include="*.py"`，可能匹配到 `__pycache__/*.pyc` 编译缓存，那不是源码。

---

## 4. 相机图规格符合性（冻结规格）

| 检查项 | 要求 | 实际 | 通过 |
|---|---|---|---|
| 图尺寸 | 640×480 | 640×480 | ✓ |
| 格式 | JPEG | JPEG | ✓ |
| 车牌底色/字色 | 蓝底(18,84,163)白字 | 一致 | ✓ |
| 车牌尺寸 | 260×82 | 260×82 | ✓ |
| 字体 | 微软雅黑 Bold 52pt | msyhbd 52pt | ✓ |
| 水印 | 右下角 `{camera_id} {虚拟时间}` | 一致 | ✓ |

> 该规格经 A 实测 HyperLPR3 识别 3/3（置信度 0.958–0.981）；改动前须重跑 `tests/check_lpr.py`。

---

## 5. 已知事项

1. **计费口径**：免费 15 分钟**从入场时刻扣除**（例2 = 7.50）；统一走 `common/pricing.py`。
2. `mock_service/` 为完整实现；常量表 `CAMERA_TABLE` / `INITIAL_OCCUPIED` 为固定演示数据。
3. 早期 stub 冒烟脚本已随代码精简移除（断言已并入 `tests/smoke_mock.py`）。
4. 本记录覆盖全部接口 + 计费 + 时钟纪律 + 相机图规格。
5. **v1.3 记录库（10-14）**：票据/预约落盘 `data/mock.sqlite`（进入时间/离开时间/缴费状态/预约情况），
   进程重启自动恢复在场与未过期预约；`/debug/reset` 清库回初始。冒烟脚本经 `MOCK_DB` 环境变量
   写独立库（`data/mock_smoke.sqlite`），与运行中服务互不干扰。
