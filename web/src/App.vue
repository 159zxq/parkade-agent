<template>
  <div class="app-shell">
    <!-- 顶栏 -->
    <header class="app-header">
      <div class="brand">🅿️ 停车场管理智能体</div>
      <div class="header-right">
        <el-tag :type="health.mock ? 'success' : 'danger'" effect="dark" size="small">
          {{ health.mock ? 'Mock 在线' : 'Mock 离线' }}
        </el-tag>
        <el-tag type="info" effect="dark" size="small">虚拟时间 {{ health.virtualTime || '—' }}</el-tag>
      </div>
    </header>

    <main class="app-main">
      <!-- ───── 左：用户 ───── -->
      <section class="card users-pane">
        <div class="card-title">
          <span class="t"><el-icon><User /></el-icon>用户</span>
          <el-button size="small" type="primary" :icon="Plus" circle @click="addUserDialog = true" />
        </div>
        <div class="user-list">
          <div v-for="u in users" :key="u.id" class="user-item"
               :class="{ active: u.id === currentUser?.id }" @click="switchUser(u)">
            <div class="avatar">{{ u.name.slice(0, 1) }}</div>
            <div class="uname">
              <div>{{ u.name }}</div>
              <div class="uplate">{{ u.plate || '未登记车牌' }}</div>
            </div>
            <el-icon class="del" @click.stop="removeUser(u)"><Delete /></el-icon>
          </div>
        </div>
      </section>

      <!-- ───── 中：聊天 ───── -->
      <section class="card chat-pane">
        <div class="card-title">
          <span class="t">
            <el-icon><ChatDotRound /></el-icon>
            对话{{ currentUser ? ` · ${currentUser.name}` : '' }}
            <el-tag v-if="currentUser?.plate" size="small" type="info" effect="plain">
              {{ currentUser.plate }}
            </el-tag>
          </span>
          <el-button size="small" text type="danger" :icon="Delete" @click="clearChat">清空对话</el-button>
        </div>
        <div ref="chatBody" class="chat-body">
          <div v-if="!messages.length" class="chat-empty">
            {{ currentUser ? `你好，${currentUser.name}。可以问我你的车辆状态、停车费用或停车场规章。` : '请先选择或添加用户' }}
          </div>
          <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">
            <div class="who">{{ m.role === 'user' ? '🧑' : '🤖' }}</div>
            <div class="bubble">
              {{ m.text }}
              <div v-if="m.snippets && m.snippets.length" class="snippets">
                <el-collapse>
                  <el-collapse-item :title="`引用片段（${m.snippets.length} 条）`">
                    <div v-for="sn in m.snippets" :key="sn.chunk_id" class="sn">
                      <b>[{{ sn.chunk_id }}]</b> {{ sn.source }}<br />{{ sn.text }}
                    </div>
                  </el-collapse-item>
                </el-collapse>
              </div>
            </div>
          </div>
          <div v-if="chatting" class="msg assistant">
            <div class="who">🤖</div>
            <div class="bubble"><el-icon class="is-loading"><Loading /></el-icon> 思考中…</div>
          </div>
        </div>
        <div class="chat-input-row">
          <el-input v-model="input" size="large" placeholder="输入问题，Enter 发送"
                    :disabled="chatting || !currentUser" @keyup.enter="send" />
          <el-button size="large" type="primary" :icon="Promotion" :loading="chatting"
                     :disabled="!currentUser" @click="send">发送</el-button>
        </div>
      </section>

      <!-- ───── 第三栏：车牌识别 ───── -->
      <section class="card recog-pane">
        <div class="card-title">
          <span class="t"><el-icon><Camera /></el-icon>车牌识别（双通道）</span>
        </div>
        <div class="upload-row">
          <div class="gate-row">
            <el-radio-group v-model="uploadAction" size="small">
              <el-radio-button value="entry">入场</el-radio-button>
              <el-radio-button value="exit">出场验票</el-radio-button>
            </el-radio-group>
            <el-select v-model="uploadArea" :disabled="uploadAction !== 'entry'"
                       size="small" style="width: 150px; flex: none">
              <el-option value="auto" label="自动分配区域" />
              <el-option value="A" label="A 区 · 地面区" />
              <el-option value="B" label="B 区 · 地下一层" />
              <el-option value="C" label="C 区 · 地下二层" />
            </el-select>
          </div>
          <el-upload drag :show-file-list="false" accept="image/png,image/jpeg"
                     :before-upload="onUpload" :disabled="recognizing">
            <el-icon style="font-size: 34px; color: #93c5fd"><UploadFilled /></el-icon>
            <div style="font-size: 13px; color: var(--muted); margin-top: 6px">
              点击或拖拽上传抓拍图片（模拟硬件抓拍图）<br />
              <span style="font-size: 12px">
                识别成功即{{ uploadAction === 'exit'
                  ? '出场验票（未结清费用不予放行）'
                  : `登记入场（${uploadArea === 'auto' ? '自动分配区域' : uploadArea + ' 区'}）` }}
                · 支持 PNG / JPEG，≤10MB
              </span>
            </div>
          </el-upload>
        </div>
        <div v-if="recog" class="recog-result">
          <el-tag :type="BADGES[recog.source]?.type || 'info'" size="large" effect="dark" style="align-self: center">
            {{ BADGES[recog.source]?.text || recog.source }}
          </el-tag>
          <div v-if="recog.plate" class="recog-plate">{{ recog.plate }}</div>
          <div class="recog-detail">
            <el-tag type="info" effect="plain">本地 OCR：{{ recog.ocr?.plate || '未识别' }}</el-tag>
            <el-tag type="info" effect="plain">视觉模型：{{ recog.vlm?.plate || '未识别' }}</el-tag>
            <el-tag type="info" effect="plain">{{ recog.elapsed_ms }} ms</el-tag>
          </div>
          <el-alert v-if="gate" :title="gate.message" :type="gate.ok ? 'success' : 'warning'"
                    :closable="false" show-icon />
          <el-alert v-if="recog.error" :title="recog.error" type="warning" :closable="false" show-icon />
          <img v-if="recog.image_url" :src="recog.image_url" class="recog-img" alt="抓拍图片" />
        </div>
      </section>

      <!-- ───── 第四栏：缴费 + 车位看板 ───── -->
      <div class="right-pane">

        <!-- 缴费 -->
        <section class="card">
          <div class="card-title">
            <span class="t"><el-icon><Wallet /></el-icon>缴费</span>
            <span style="font-size: 12px; color: var(--muted)">先缴清 · 后出场</span>
          </div>
          <div class="pay-body">
            <div class="pay-row">
              <el-input v-model="payPlate" placeholder="车牌号，如 京A12345" style="flex: 1"
                        :disabled="paying" @keyup.enter="doPay" />
              <el-button type="primary" :icon="Wallet" :loading="paying" @click="doPay">
                {{ payType === 'spot' ? '现缴' : '办月卡' }}
              </el-button>
            </div>
            <div class="pay-row">
              <el-radio-group v-model="payType" size="small">
                <el-radio-button value="spot">现缴（出场费）</el-radio-button>
                <el-radio-button value="monthly">月卡</el-radio-button>
              </el-radio-group>
              <el-button v-if="payType === 'spot'" size="small" :icon="Refresh"
                         :loading="feeLoading" @click="doQueryFee">试算 {{ feeText }}</el-button>
              <el-tag v-else type="warning" effect="plain" size="small">30 天有效期内出场免计费</el-tag>
            </div>
            <div v-if="payHint" class="pay-hint"
                 :class="{ ok: payHint.ok, warn: !payHint.ok }">{{ payHint.text }}</div>
            <div style="font-size: 12px; color: var(--muted)">
              现缴：结清当前待缴费用（仅在场车）；<b>未结清的车出场时闸机不予放行</b>。
              月卡：统一价 300 元/月（不分车型），有效期内停车不计费。
            </div>
          </div>
        </section>

        <!-- 车位看板 -->
        <section class="card">
          <div class="card-title">
            <span class="t"><el-icon><DataLine /></el-icon>车位看板</span>
            <span>
              <el-button size="small" :icon="Tickets" @click="openRecords">记录</el-button>
              <el-button size="small" :icon="Setting" @click="openBoardDebug">调试</el-button>
            </span>
          </div>
          <div class="board-body">
            <div v-for="a in lots" :key="a.area" class="area-row">
              <div class="area-head">
                <span class="name">{{ a.area }} 区 · {{ a.name }}</span>
                <span>
                  空闲 <b style="color: var(--brand)">{{ a.free }}</b> / {{ a.total }}
                  <el-tag v-if="a.reserved" size="small" type="warning" effect="plain" style="margin-left: 6px">
                    预约保留 {{ a.reserved }}
                  </el-tag>
                </span>
              </div>
              <el-progress :percentage="a.total ? Math.round(a.free / a.total * 100) : 0"
                           :stroke-width="12" :show-text="false"
                           :color="a.free === 0 ? '#ef4444' : a.free / a.total < 0.2 ? '#f59e0b' : '#3b82f6'" />
            </div>
            <div style="font-size: 12px; color: var(--muted); text-align: right">
              更新时间：{{ lotsUpdated }}（自动刷新）
            </div>
          </div>
          <div class="board-foot">
            <el-button size="small" :icon="Refresh" @click="loadLots">刷新</el-button>
            <el-button size="small" type="warning" plain @click="timeOffset(120)">时钟 +2 小时</el-button>
            <el-button size="small" type="danger" plain @click="resetAll">全部复位</el-button>
          </div>
        </section>
      </div>
    </main>

    <!-- 添加用户 -->
    <el-dialog v-model="addUserDialog" title="添加用户" width="420px">
      <el-form label-width="68px">
        <el-form-item label="用户名" required>
          <el-input v-model="newUserName" placeholder="输入用户名" maxlength="20" />
        </el-form-item>
        <el-form-item label="车牌号" required>
          <el-input v-model="newUserPlate" placeholder="如 京A12345（用于判断车辆是否在场）"
                    maxlength="10" @keyup.enter="confirmAddUser" />
        </el-form-item>
      </el-form>
      <div style="font-size: 12px; color: var(--muted); margin-top: -8px; padding-left: 68px">
        系统会以此车牌实时查询车辆是否在场、已停多久
      </div>
      <template #footer>
        <el-button @click="addUserDialog = false">取消</el-button>
        <el-button type="primary" @click="confirmAddUser">添加</el-button>
      </template>
    </el-dialog>

    <!-- 看板调试 -->
    <el-dialog v-model="boardDebugDialog" title="车位看板调试（直接调整 Mock 真实状态）" width="560px">
      <div class="dialog-section">
        <h4>各区域总车位 / 占用数</h4>
        <div v-for="a in lots" :key="a.area" class="area-debug-row">
          <span class="aname">{{ a.area }} 区</span>
          <el-input-number v-model="debugForm[a.area].total" :min="0" :max="999" size="small"
                           @change="applyDebug(a.area)" />
          <span style="font-size: 12px; color: var(--muted)">总车位</span>
          <el-input-number v-model="debugForm[a.area].occupied" :min="0" :max="debugForm[a.area].total"
                           size="small" @change="applyDebug(a.area)" />
          <span style="font-size: 12px; color: var(--muted)">占用</span>
        </div>
        <div style="font-size: 12px; color: var(--muted)">
          提示：调整即写入 Mock 真实状态，Agent 查询会同步反映；占用数减少时优先移除调试合成车牌。
        </div>
      </div>
      <div class="dialog-section">
        <h4>虚拟时间（用于演示计费）</h4>
        <div class="time-row">
          <el-date-picker v-model="debugTime" type="datetime" placeholder="选择虚拟时间"
                          format="YYYY-MM-DD HH:mm:ss" value-format="YYYY-MM-DDTHH:mm:ss" />
          <el-button type="primary" size="small" @click="applyTime">应用</el-button>
          <el-button size="small" @click="timeOffset(120)">+2 小时</el-button>
          <el-button size="small" @click="timeOffset(-120)">-2 小时</el-button>
          <el-button size="small" type="info" plain @click="resetTime">时间复位</el-button>
        </div>
        <div style="font-size: 12px; color: var(--muted)">
          当前虚拟时间：{{ health.virtualTime || '—' }}（真实时间：{{ health.realTime || '—' }}）
        </div>
      </div>
      <template #footer>
        <el-button type="danger" plain @click="resetAll">全部复位（车位+票据+记录+时间）</el-button>
        <el-button type="primary" @click="boardDebugDialog = false">完成</el-button>
      </template>
    </el-dialog>

    <!-- 进出场 / 缴费 / 预约 记录 -->
    <el-dialog v-model="recordsDialog" title="进出场记录（落盘数据库）" width="720px" top="6vh">
      <div class="dialog-section">
        <div style="display: flex; gap: 10px; align-items: center; margin-bottom: 10px">
          <el-select v-model="recordsPlate" clearable placeholder="全部车牌" style="width: 220px"
                     @change="loadRecords">
            <el-option v-for="u in users" :key="u.id" :value="u.plate" :label="`${u.plate}（${u.name}）`" />
          </el-select>
          <el-button type="primary" size="small" :icon="Refresh" @click="loadRecords">刷新</el-button>
          <span style="font-size: 12px; color: var(--muted)">
            进入时间/离开时间/缴费状态（离开默认已缴费）——来自 mock 落盘库，重启不丢
          </span>
        </div>
        <h4>票据（进入 / 离开 / 缴费）</h4>
        <el-table :data="records.tickets" size="small" max-height="260">
          <el-table-column prop="ticket_id" label="票号" width="82" />
          <el-table-column prop="plate" label="车牌" width="106" />
          <el-table-column prop="area" label="区" width="44" />
          <el-table-column prop="entry_time" label="进入时间" width="152" />
          <el-table-column prop="exit_time" label="离开时间" width="152">
            <template #default="{ row }">{{ row.exit_time || '在场中' }}</template>
          </el-table-column>
          <el-table-column label="费用" width="76">
            <template #default="{ row }">{{ row.fee == null ? '—' : row.fee.toFixed(2) }}</template>
          </el-table-column>
          <el-table-column label="缴费" width="86">
            <template #default="{ row }">
              <el-tag v-if="row.status === 'active'" size="small" type="info" effect="plain">在场</el-tag>
              <el-tag v-else-if="row.paid" size="small" type="success" effect="plain">已缴费</el-tag>
              <el-tag v-else size="small" type="warning" effect="plain">未缴费</el-tag>
            </template>
          </el-table-column>
        </el-table>
        <h4 style="margin-top: 14px">预约</h4>
        <el-table :data="records.reservations" size="small" max-height="180">
          <el-table-column prop="reserve_id" label="预约号" width="82" />
          <el-table-column prop="plate" label="车牌" width="106" />
          <el-table-column prop="area" label="区" width="44" />
          <el-table-column prop="reserve_time" label="预约时间" width="152" />
          <el-table-column prop="keep_until" label="保留至" width="152" />
          <el-table-column label="状态">
            <template #default="{ row }">
              <el-tag :type="{ active: 'success', consumed: 'primary', expired: 'info' }[row.status]"
                      size="small" effect="plain">
                {{ { active: '有效', consumed: '已核销', expired: '已过期' }[row.status] || row.status }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
        <h4 style="margin-top: 14px">缴费（现缴 / 月卡）</h4>
        <el-table :data="records.payments" size="small" max-height="180">
          <el-table-column prop="payment_id" label="缴费号" width="82" />
          <el-table-column prop="plate" label="车牌" width="106" />
          <el-table-column label="类型" width="76">
            <template #default="{ row }">
              <el-tag :type="row.type === 'monthly' ? 'warning' : 'primary'" size="small" effect="plain">
                {{ row.type === 'monthly' ? '月卡' : '现缴' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="金额" width="86">
            <template #default="{ row }">¥{{ row.amount.toFixed(2) }}</template>
          </el-table-column>
          <el-table-column prop="pay_time" label="缴费时间" width="152" />
          <el-table-column label="有效期" width="152">
            <template #default="{ row }">{{ row.valid_until || '—' }}</template>
          </el-table-column>
        </el-table>
      </div>
      <template #footer>
        <el-button type="primary" @click="recordsDialog = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Delete, ChatDotRound, Promotion, Loading, Camera, UploadFilled, DataLine, Setting, Refresh, User, Tickets, Wallet } from '@element-plus/icons-vue'
import api from './api'

const BADGES = {
  both_agree: { text: '双通道一致', type: 'success' },
  both_conflict: { text: '存在分歧（以 OCR 为准）', type: 'warning' },
  ocr_only: { text: '仅本地 OCR', type: 'info' },
  vlm_only: { text: '视觉模型兜底', type: 'primary' },
  failed: { text: '识别失败，请重拍/换图', type: 'danger' }
}

// ── 用户 ──
const users = ref([])
const currentUser = ref(null)
const addUserDialog = ref(false)
const newUserName = ref('')
const newUserPlate = ref('')
let timer = null

async function loadUsers() {
  const { data } = await api.get('/users')
  users.value = data.users
  if (!currentUser.value && users.value.length) {
    switchUser(users.value[0])
  } else if (currentUser.value) {
    // 保持当前选择（刷新列表后同步最新字段）
    const again = users.value.find(x => x.id === currentUser.value.id)
    if (again) currentUser.value = again
  }
}

async function switchUser(u) {
  currentUser.value = u
  await loadMessages()
}

const PLATE_RE = /^[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼][A-HJ-NP-Z][A-HJ-NP-Z0-9]{4,6}$/

async function confirmAddUser() {
  const name = newUserName.value.trim()
  const plate = newUserPlate.value.replace(/\s/g, '').toUpperCase()
  if (!name) return ElMessage.warning('请输入用户名')
  if (!plate) return ElMessage.warning('必须填写车牌号（用于判断车辆是否在场）')
  if (!PLATE_RE.test(plate)) return ElMessage.warning('车牌格式不正确，示例：京A12345')
  try {
    const { data } = await api.post('/users', { name, plate })
    ElMessage.success(`已添加用户「${name}」（车牌 ${plate}）`)
    addUserDialog.value = false
    newUserName.value = ''
    newUserPlate.value = ''
    await loadUsers()
    const created = users.value.find(x => x.id === data.user.id)
    if (created) await switchUser(created)
  } catch (e) {
    ElMessage.error(e.response?.data?.error || '添加失败')
  }
}

async function removeUser(u) {
  try {
    await ElMessageBox.confirm(`删除用户「${u.name}」及其对话记录？`, '确认删除', { type: 'warning' })
  } catch { return }
  await api.delete(`/users/${u.id}`)
  ElMessage.success('已删除')
  if (currentUser.value?.id === u.id) currentUser.value = null
  await loadUsers()
  if (currentUser.value) await loadMessages()
}

// ── 聊天 ──
const messages = ref([])
const input = ref('')
const chatting = ref(false)
const chatBody = ref(null)

async function loadMessages() {
  if (!currentUser.value) { messages.value = []; return }
  const { data } = await api.get(`/users/${currentUser.value.id}/messages`)
  messages.value = data.messages
  await scrollBottom()
}

async function scrollBottom() {
  await nextTick()
  if (chatBody.value) chatBody.value.scrollTop = chatBody.value.scrollHeight
}

async function send() {
  const msg = input.value.trim()
  if (!msg || chatting.value || !currentUser.value) return
  input.value = ''
  messages.value.push({ role: 'user', text: msg, snippets: [] })
  chatting.value = true
  await scrollBottom()
  try {
    const { data } = await api.post('/chat', { user_id: currentUser.value.id, message: msg })
    messages.value.push({ role: 'assistant', text: data.reply, snippets: data.snippets })
  } catch (e) {
    messages.value.push({ role: 'assistant', text: `（请求失败：${e.response?.data?.error || e.message}）`, snippets: [] })
  } finally {
    chatting.value = false
    await scrollBottom()
    loadLots()   // 对话中可能发生入场/预约/出场 → 看板即时同步
  }
}

async function clearChat() {
  if (!currentUser.value) return
  try {
    await ElMessageBox.confirm('清空当前用户的对话记录？（Agent 记忆仍保留）', '确认', { type: 'warning' })
  } catch { return }
  await api.delete(`/users/${currentUser.value.id}/messages`)
  messages.value = []
  ElMessage.success('已清空界面记录')
}

// ── 识别 ──
const recognizing = ref(false)
const recog = ref(null)
const gate = ref(null)   // 识别成功后的闸机动作结果（入场/出场）
const uploadAction = ref('entry')   // 上传动作：entry=登记入场 / exit=结算出场
const uploadArea = ref('auto')      // 上传入场目标区域：auto=自动分配 / A/B/C

// ── 缴费（现缴 / 月卡）──
const payPlate = ref('')
const payType = ref('spot')     // spot=现缴（出场费）/ monthly=月卡
const paying = ref(false)
const feeLoading = ref(false)
const feeText = ref('查询')
const payHint = ref(null)

async function doQueryFee() {
  const plate = payPlate.value.replace(/\s/g, '').toUpperCase()
  if (!plate) return ElMessage.warning('请输入车牌号')
  feeLoading.value = true
  try {
    const { data } = await api.post('/fee', { plate })
    if (data.ok) {
      const due = data.due ?? data.total
      const paidAmount = data.paid_amount ?? 0
      feeText.value = `待缴 ¥${due.toFixed(2)}`
      payHint.value = { ok: due <= 0.001,
        text: due > 0.001
          ? `${plate} 在场，应缴 ¥${data.total.toFixed(2)}（${data.duration_min} 分钟），`
            + `已结清 ¥${Number(paidAmount).toFixed(2)}，待缴 ¥${due.toFixed(2)}（缴清后出口放行）`
          : `${plate} 已结清当前停车费（应缴 ¥${data.total.toFixed(2)}），出口可直接放行` }
      if (due <= 0.001) feeText.value = '已结清'
    } else {
      feeText.value = '查询'
      payHint.value = { ok: false, text: data.error?.message || '查询失败' }
    }
  } catch (e) {
    feeText.value = '查询'
    payHint.value = { ok: false, text: e.response?.data?.error?.message || '查询失败' }
  } finally {
    feeLoading.value = false
  }
}

async function doPay() {
  const plate = payPlate.value.replace(/\s/g, '').toUpperCase()
  if (!plate) return ElMessage.warning('请输入车牌号')
  paying.value = true
  try {
    const { data } = await api.post('/pay', { plate, type: payType.value })
    if (data.ok) {
      if (data.type === 'monthly') {
        payHint.value = { ok: true,
          text: `月卡办理成功：${plate} ¥${data.amount.toFixed(2)}（缴费号 ${data.payment_id}）`
                + ` · 有效至 ${data.valid_until} · 有效期内出场免计费` }
        ElMessage.success(`月卡办理成功 ¥${data.amount.toFixed(2)}（30 天）`)
      } else {
        payHint.value = { ok: true,
          text: `现缴成功：${plate} 缴 ¥${data.amount.toFixed(2)}（缴费号 ${data.payment_id}）`
                + ` · 已结清，出口闸机可放行` }
        ElMessage.success(`现缴成功 ¥${data.amount.toFixed(2)}，可出场`)
      }
      recog.value = null
      gate.value = null
      loadLots()
    } else {
      // 已结清等业务提示：提示栏红色展示
      payHint.value = { ok: false, text: data.error?.message || '缴费失败' }
    }
  } catch (e) {
    payHint.value = { ok: false, text: e.response?.data?.error?.message || '缴费失败' }
  } finally {
    paying.value = false
  }
}

function applyRecognize(data) {
  recog.value = data.result
  gate.value = data.gate || null
  if (data.gate) {
    if (data.gate.ok) {
      ElMessage.success(data.gate.message)
    } else {
      ElMessage.warning(data.gate.message)
    }
  }
  loadLots()   // 入场/出场后看板即时刷新（记录库同时留痕）
}

async function onUpload(file) {
  recognizing.value = true
  try {
    const form = new FormData()
    form.append('file', file)
    form.append('action', uploadAction.value)
    if (uploadAction.value === 'entry') {
      form.append('area', uploadArea.value === 'auto' ? '' : uploadArea.value)
    }
    const { data } = await api.post('/recognize', form)
    applyRecognize(data)
  } catch (e) {
    ElMessage.error(e.response?.data?.error || '识别请求失败')
  } finally {
    recognizing.value = false
  }
  return false // 阻止 el-upload 默认上传
}

// ── 看板 ──
const lots = ref([])
const lotsUpdated = ref('—')
const health = reactive({ mock: true, virtualTime: '', realTime: '' })
const boardDebugDialog = ref(false)
const debugForm = reactive({})
const debugTime = ref(null)

// ── 记录（进出场/缴费/预约，来自 mock 落盘库）──
const recordsDialog = ref(false)
const recordsPlate = ref('')
const records = reactive({ tickets: [], reservations: [], payments: [] })

async function loadRecords() {
  try {
    const params = { limit: 30 }
    if (recordsPlate.value) params.plate = recordsPlate.value
    const { data } = await api.get('/records', { params })
    if (data.ok) {
      records.tickets = data.tickets
      records.reservations = data.reservations
      records.payments = data.payments || []
    }
  } catch {
    ElMessage.error('记录查询失败（Mock 服务不可达？）')
  }
}

async function openRecords() {
  await loadRecords()
  recordsDialog.value = true
}

async function loadLots() {
  try {
    const { data } = await api.get('/lots')
    if (data.ok) {
      lots.value = data.areas
      lotsUpdated.value = data.updated_at
      for (const a of data.areas) {
        if (!debugForm[a.area]) debugForm[a.area] = { total: a.total, occupied: a.occupied }
        else { debugForm[a.area].total = a.total; debugForm[a.area].occupied = a.occupied }
      }
    }
  } catch { /* 状态条会提示 */ }
  await loadHealth()
}

async function loadHealth() {
  try {
    const { data } = await api.get('/health')
    health.mock = data.mock
    const t = await api.get('/debug/time')
    health.virtualTime = t.data.virtual_time
    health.realTime = t.data.real_time
  } catch { health.mock = false }
}

async function openBoardDebug() {
  await loadLots()
  boardDebugDialog.value = true
}

async function applyDebug(area) {
  const f = debugForm[area]
  try {
    const { data } = await api.post('/debug/lots', { area, total: f.total, occupied: f.occupied })
    if (data.ok) {
      ElMessage.success(`${area} 区已更新：${data.occupied}/${data.total}`)
      await loadLots()
    } else {
      ElMessage.error(data.error?.message || '调整失败')
      await loadLots()
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.error?.message || '调整失败')
    await loadLots()
  }
}

async function timeOffset(minutes) {
  await api.post('/debug/time', { offset_minutes: minutes })
  ElMessage.success(`虚拟时间 ${minutes > 0 ? '+' : ''}${minutes} 分钟`)
  await loadHealth()
}

async function applyTime() {
  if (!debugTime.value) return ElMessage.warning('请选择时间')
  await api.post('/debug/time', { set: debugTime.value })
  ElMessage.success('虚拟时间已设置')
  await loadHealth()
}

async function resetTime() {
  await api.post('/debug/time/reset')
  debugTime.value = null
  ElMessage.success('时间已复位')
  await loadHealth()
}

async function resetAll() {
  await api.post('/debug/reset')
  recog.value = null
  gate.value = null
  ElMessage.success('车位/票据/记录/时间已全部复位')
  await loadLots()
}

onMounted(async () => {
  await loadUsers()
  await loadLots()
  // 看板 5 秒轮询（loadLots 内含 loadHealth）：Agent 对话里入场/预约、相机链路状态变化都会实时反映
  timer = setInterval(loadLots, 5000)
})

onUnmounted(() => clearInterval(timer))
</script>
