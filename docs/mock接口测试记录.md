# Mock 服务接口测试记录（TC-Mock）

> 负责人：成员B｜模块：`mock_service/`｜执行脚本：`scripts/smoke_mock.py`、`scripts/test_pricing.py`
> 对接依据：《接口契约》v1 §3（字段与规则冻结）
> 覆盖：B1–B6 全部接口 + 计费一致性 + 时钟纪律
>
> 执行方式（项目根目录）：
>
> ```bash
> .venv/Scripts/python.exe scripts/smoke_mock.py      # 接口冒烟 33 项
> .venv/Scripts/python.exe scripts/test_pricing.py    # 计费自测 9 项
> ```
>
> 最近执行：**2026-10-12** → 接口 **ALL PASS（共 33 项）**、计费 **ALL PASS（共 9 项）**。
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

**接口用例合计 33 项，全部通过。**

---

## 2. 计费一致性用例（TC-Price-xx，`common/pricing.py` 由成员A 维护）

执行 `python scripts/test_pricing.py` → **ALL PASS（共 9 项）**。

| 用例号 | 场景 | 期望 | 实际 | 通过 |
|---|---|---|---|---|
| TC-Price-01 | 小型车 10:00→12:00（契约 §4 样例1） | 12.00 元 / 4 单位 | 12.0 / 4 | ✓ |
| TC-Price-02 | 小型车 21:30→23:10（跨夜间，样例2） | 7.50 元 / 4 单位 | 7.5 / 4 | ✓ |
| TC-Price-03 | 小型车 11-10 21:00→11-11 01:00（跨天，样例3） | 15.00 元 / 8 单位 | 15.0 / 8 | ✓ |
| TC-Price-04 | 小型车 07:00→23:00（触当日封顶） | 30.00 元，cap_applied | 30.0 / True | ✓ |
| TC-Price-05 | 小型车 10:00→10:15（免费内） | 0.00 元 | 0.0 | ✓ |
| TC-Price-06 | 小型车 10:00→10:16（超 15 分钟） | 3.00 元 / 1 单位 | 3.0 / 1 | ✓ |
| TC-Price-07 | 中型车 10:00→12:00 | 20.00 元 / 4 单位 | 20.0 / 4 | ✓ |
| TC-Price-08 | 月卡车 | 0.00 元 | 0.0 | ✓ |
| TC-Price-09 | 未知车型 | 抛 ValueError | ValueError | ✓ |

---

## 3. 时钟纪律自查（契约 §0 铁律）

```bash
grep -rn "datetime.now()" mock_service/ --include="*.py" | grep -v clock.py
```

结果：**输出为空** —— 源码中 `datetime.now()` 仅出现在 `mock_service/clock.py`（时钟实现本体），
其余业务代码一律使用 `clock.now()`，符合契约 §0 虚拟时钟纪律。

> 注：若不加 `--include="*.py"`，可能匹配到 `__pycache__/*.pyc` 编译缓存，那不是源码。

---

## 4. 相机图规格符合性（契约 §2 冻结规格）

| 检查项 | 要求 | 实际 | 通过 |
|---|---|---|---|
| 图尺寸 | 640×480 | 640×480 | ✓ |
| 格式 | JPEG | JPEG | ✓ |
| 车牌底色/字色 | 蓝底(18,84,163)白字 | 一致 | ✓ |
| 车牌尺寸 | 260×82 | 260×82 | ✓ |
| 字体 | 微软雅黑 Bold 52pt | msyhbd 52pt | ✓ |
| 水印 | 右下角 `{camera_id} {虚拟时间}` | 一致 | ✓ |

> 该规格经 A 实测 HyperLPR3 识别 3/3（置信度 0.958–0.981）；改动前须重跑 `scripts/check_lpr.py`。

---

## 5. 已知事项

1. **计费口径**：免费 15 分钟**从入场时刻扣除**（契约 §4 v0.1），例2=7.50；统一走 A 的 `common/pricing.py`。
2. `scripts/smoke_stub.py`（A 的 stub 冒烟）中 `/exit=12.00` 一项现将 FAIL —— **预期内**：
   stub 写死"入场 2 小时前"，真实现按真实入场时间计费，该文件使命已完成。
3. 本记录覆盖 B1–B6；B7b 位次即为本文件的定稿版本。
