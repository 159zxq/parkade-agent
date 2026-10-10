# Mock 服务接口测试记录（TC-Mock）

> 负责人：成员B｜模块：`mock_service/`｜执行脚本：`scripts/smoke_mock.py`
> 对接依据：《接口契约》v1 §3（字段与规则冻结）
> 执行方式（项目根目录）：
>
> ```bash
> .venv/Scripts/python.exe scripts/smoke_mock.py
> ```
>
> 最近一次执行：**2026-10-11，结果 ALL PASS (28 cases)**，以下「实际」栏均为脚本真实输出。

## 1. 用例表

| 用例号 | 操作 | 预期 | 实际 | 通过 |
|---|---|---|---|---|
| TC-Mock-00 | `POST /debug/reset` | `ok:true`，状态归位 | `ok:true` | ✓ |
| TC-Mock-01 | `GET /lots/status` | A/B/C 初始 free = 27 / 52 / 58 | 27 / 52 / 58 | ✓ |
| TC-Mock-02 | `POST /entry {"plate":"京Z99999"}` | `ok:true`，ticket_id=T-0001，area=A | 一致 | ✓ |
| TC-Mock-02c | 入场后再查 `?area=A` | free=26 | 26 | ✓ |
| TC-Mock-03 | 重复入场同一车牌 | 400 `ALREADY_INSIDE` | 400 `ALREADY_INSIDE` | ✓ |
| TC-Mock-04 | `POST /debug/time {"set":"2026-11-10T10:00:00"}` | virtual_time 为该值 | 一致 | ✓ |
| TC-Mock-05 | 注入时间后 `POST /entry` | entry_time 取虚拟时间 | `2026-11-10T10:00:00` | ✓ |
| TC-Mock-06 | `POST /debug/time {"offset_minutes":120}` | virtual_time=12:00:00 | 一致 | ✓ |
| TC-Mock-07 | `POST /exit {"plate":"京A12345","dry_run":true}` | total=12.00，明细 105 分/4 单位 | 12.0 / 105 / 4 | ✓ |
| TC-Mock-07c | dry_run 后查车位 | 车仍在场（free 不变） | free=25 | ✓ |
| TC-Mock-08 | `POST /exit`（真出场） | total=12.00 | 12.0 | ✓ |
| TC-Mock-08b | 出场后查 `?area=A` | free 回补 | 26 | ✓ |
| TC-Mock-09 | 对不在场车牌出场 | 400 `NOT_INSIDE` | 400 `NOT_INSIDE` | ✓ |
| TC-Mock-10 | `POST /reserve` 京B88888 @18:00 | keep_until=18:30，reserve_id=R-0001 | 一致 | ✓ |
| TC-Mock-11 | 同车牌重复预约 | 400 `RESERVE_CONFLICT` | 400 `RESERVE_CONFLICT` | ✓ |
| TC-Mock-12 | `GET /debug/truth/cam-1` | plate=京A12345，小型车 | 一致 | ✓ |
| TC-Mock-13 | `GET /camera/cam-1/capture?variant=normal\|blur\|occluded` | 三变体均返回 image/jpeg，>1KB | 均通过（640×480 JPEG） | ✓ |
| TC-Mock-14 | `GET /camera/cam-9/capture` | 404 `CAMERA_NOT_FOUND` | 404 `CAMERA_NOT_FOUND` | ✓ |
| TC-Mock-15 | `POST /debug/time/reset` | offset_minutes=0 | 0 | ✓ |
| TC-Mock-16 | 注入 +90 后 `GET /debug/time` | offset_minutes=90 | 90 | ✓ |
| TC-Mock-17 | 全状态 reset 后查车位 | 恢复 A.free=27 | 27 | ✓ |

## 2. 计费一致性用例（`common/pricing.py` 契约 §4 三条工作示例）

执行 `.venv/Scripts/python.exe common/pricing.py`，输出 **ALL PASS**：

| 场景 | 期望 | 实际 |
|---|---|---|
| 小型车 10:00→12:00 | 12.00 元 | 12.00 ✓ |
| 小型车 21:30→23:10 | 6.00 元 | 6.00 ✓ |
| 小型车 11-10 21:00→11-11 01:00 | 15.00 元 | 15.00 ✓ |

## 3. 铁律自查

```bash
grep -rn "datetime.now()" mock_service/
```

结果：仅 `mock_service/clock.py` 命中（时钟实现本体），**其余文件零命中**，符合契约 §0 虚拟时钟纪律。

## 4. 已知事项 / 待成员A 复核

1. **`common/pricing.py` 与 `common/rates.yaml` 为 B 代写的临时版本**（红线：归属 A）。
   请 A 用正式版本整体替换；替换后重跑本记录 §2 与冒烟脚本即可回归。
2. **免费时长扣除位置**：契约正文写「从入场时刻起扣除」，但契约 §4 的三条工作示例
   （明确「直接作为测试断言」）要求免费时长从**计费时间轴末尾**倒数扣除。
   本实现以「示例即断言」为准，采用末尾倒数扣除。**请 A 确认契约正文与示例的口径**。
   （详见 `common/pricing.py` 模块 docstring 内说明。）
3. **预约与入场关系**：契约未规定预约是否阻止重复入场，本实现按字面规则
   （入场仅校验重复入场 + 空位），预约不占用车位、不绑定票据。如需「已预约不可再入场」，
   走契约变更流程后再改。
