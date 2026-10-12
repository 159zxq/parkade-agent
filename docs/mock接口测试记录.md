# Mock 服务接口测试记录（TC-Mock）

> 负责人：成员B｜模块：`mock_service/`｜执行脚本：`scripts/smoke_mock.py`
> 对接依据：《接口契约》v1 §3（字段与规则冻结）
> 执行方式（项目根目录）：
>
> ```bash
> .venv/Scripts/python.exe scripts/smoke_mock.py
> ```
>
> 最近一次执行：**2026-10-12，结果 ALL PASS（共 33 项）**，以下「实际」栏均为脚本真实输出。
>
> 说明：10-12 已把 10-11 的临时实现**迁移到成员A 的 stub 骨架上**（A0：`mock_service/{main,state,clock}.py`
> 为 B 预留 TODO 的 stub；`common/pricing.py`、`rates.yaml` 用 A 的正式版），并**按契约 §2 冻结规格重做了
> 相机图**（蓝底白字 260×82）。计费口径以 A 的正式实现为准（免费 15 分钟从入场时刻扣）。

## 1. 用例表

| 用例号 | 操作 | 预期 | 实际 | 通过 |
|---|---|---|---|---|
| TC-Mock-00 | `POST /debug/reset` | `ok:true`，状态归位 | `ok:true` | ✓ |
| TC-Mock-01 | `GET /lots/status` | 返回 A/B/C 三区 | A/B/C | ✓ |
| TC-Mock-01b/c/d | 初始车位 | A/B/C free = 27 / 52 / 58 | 27 / 52 / 58 | ✓ |
| TC-Mock-01e | `?area=a`（小写） | 仅返回 A 区 | [A] | ✓ |
| TC-Mock-01f | `?area=Z` 未知区域 | 400 `INVALID_PARAM` | 一致 | ✓ |
| TC-Mock-02 | `POST /entry {"plate":"京Z99999"}` | `ok:true`，ticket_id=T-0001，area=A | 一致 | ✓ |
| TC-Mock-02c | 入场后再查 `?area=A` | free=26 | 26 | ✓ |
| TC-Mock-03 | 重复入场同一车牌 | 400 `ALREADY_INSIDE` | 一致 | ✓ |
| TC-Mock-04 | `POST /debug/time {"set":"2026-11-10T10:00:00"}` | virtual_time 为该值 | 一致 | ✓ |
| TC-Mock-05 | 注入时间后 `POST /entry` | entry_time 取虚拟时间 | `2026-11-10T10:00:00` | ✓ |
| TC-Mock-06 | `POST /debug/time {"offset_minutes":120}` | virtual_time=12:00:00 | 一致 | ✓ |
| TC-Mock-07 | `POST /exit {"plate":"京A12345","dry_run":true}` | total=12.00，明细 105 分/4 单位 | 12.0 / 105 / 4 | ✓ |
| TC-Mock-07c | dry_run 后查车位 | 车仍在场（free 不变） | free=25 | ✓ |
| TC-Mock-08 | `POST /exit`（真出场） | total=12.00 | 12.0 | ✓ |
| TC-Mock-08b | 出场后查 `?area=A` | free 回补 | 26 | ✓ |
| TC-Mock-09 | 对不在场车牌出场 | 400 `NOT_INSIDE` | 一致 | ✓ |
| TC-Mock-10 | `POST /reserve` 京B88888 @18:00 | keep_until=18:30，reserve_id=R-0001 | 一致 | ✓ |
| TC-Mock-11 | 同车牌重复预约 | 400 `RESERVE_CONFLICT` | 一致 | ✓ |
| TC-Mock-20 | 契约 §4 例2：21:30→23:10 出场 | total=**7.50** | 7.5 | ✓ |
| TC-Mock-13 | `GET /camera/cam-1/capture?variant=normal\|blur\|occluded` | 三变体均返回 image/jpeg，>1KB | 均通过（640×480 JPEG） | ✓ |
| TC-Mock-12 | `GET /debug/truth/cam-1` | plate=京A12345，小型车 | 一致 | ✓ |
| TC-Mock-14 | `GET /camera/cam-9/capture` | 404 `CAMERA_NOT_FOUND` | 一致 | ✓ |
| TC-Mock-14b | `GET /camera/cam-1/capture?variant=xxx` | 400 `INVALID_PARAM` | 一致 | ✓ |
| TC-Mock-15 | `POST /debug/time/reset` | offset_minutes=0 | 0 | ✓ |
| TC-Mock-16 | 注入 +90 后 `GET /debug/time` | offset_minutes=90 | 90 | ✓ |
| TC-Mock-17 | 全状态 reset 后查车位 | 恢复 A.free=27 | 27 | ✓ |

**合计 33 项，全部通过。**

## 2. 计费一致性用例（`common/pricing.py`，A 维护）

执行 `.venv/Scripts/python.exe scripts/test_pricing.py`，输出 **ALL PASS（共 9 项）**：

| 场景 | 期望 | 实际 |
|---|---|---|
| 小型车 10:00→12:00 | 12.00 元 | 12.00 ✓ |
| 小型车 21:30→23:10（跨夜间） | **7.50 元** | 7.50 ✓ |
| 小型车 11-10 21:00→11-11 01:00（跨天） | 15.00 元 | 15.00 ✓ |
| 小型车 07:00→23:00（触封顶） | 30.00 元（cap_applied） | 30.00 ✓ |
| 小型车 10:00→10:15（免费内） | 0.00 元 | 0.00 ✓ |
| 中型车白天 2 小时 | 20.00 元 | 20.00 ✓ |
| 月卡 | 0.00 元 | 0.00 ✓ |
| 未知车型 | ValueError | 抛出 ✓ |

## 3. 相机图规格（契约 §2 冻结）

`mock_service/camera.py` 按 `scripts/check_lpr.py` 的冻结规格绘制：

- 场景 640×480 灰底 (150,150,148)；车牌 **260×82 蓝底(18,84,163)白字**，白框宽 3，**微软雅黑 Bold 52pt**，居中；
- 右下角水印 `{camera_id} {虚拟时间}`；
- 实测输出：normal/blur/occluded 三变体均为 640×480 JPEG。

> 该规格经 A 实测 HyperLPR3 识别 3/3（置信度 0.958–0.981），**改动前必须重跑 `scripts/check_lpr.py` 验证**。

## 4. 铁律自查

```bash
grep -rn "datetime.now()" mock_service/ | grep -v clock.py    # 应为空
```

结果：**仅 `mock_service/clock.py` 命中**（时钟实现本体），其余文件零命中，符合契约 §0 虚拟时钟纪律。

## 5. 已知事项

1. **计费口径已与 A 对齐**：免费 15 分钟**从入场时刻扣除**（契约 §4 已由 A 修正为 v0.1，例2 = 7.50）。
   10-11 的临时实现曾按"末尾倒数扣"得到 6.00，**已废弃**；现统一走 A 的 `common/pricing.py`。
2. `mock_service/` 现为完整实现（A 的 stub 中所有 `TODO(Bx)` 已替换为真逻辑）；
   A 的常量表 `CAMERA_TABLE` / `INITIAL_OCCUPIED` 未改动，与契约 §2/§5 一致。
3. `scripts/smoke_stub.py`（A 的 stub 冒烟）中「/exit total=12.00」一项现在会 FAIL——
   这是**预期的**：stub 里 /exit 写死"入场 2 小时前"，真实现改为按真实入场时间计费，
   刚入场即出场自然为免费 0 元。该文件的使命已完成，由 `smoke_mock.py` 取代。
   **A 决定（10-12）**：保留该文件作 A0 阶段证据链，断言改为「虚拟时间注入（10:00→+120min→12.00）」，
   不再依赖 stub 假数据；修正后 **12/12 全绿**。
