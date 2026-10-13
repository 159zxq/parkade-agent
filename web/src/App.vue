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
            <span class="uname">{{ u.name }}</span>
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
          </span>
          <el-button size="small" text type="danger" :icon="Delete" @click="clearChat">清空对话</el-button>
        </div>
        <div ref="chatBody" class="chat-body">
          <div v-if="!messages.length" class="chat-empty">
            试着问：<br />「A区还有多少车位？」<br />「让 cam-1 的车入场」<br />「小型车怎么收费？」
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

      <!-- ───── 右：识别 + 看板 ───── -->
      <div class="right-pane">
        <!-- 抓拍/上传识别 -->
        <section class="card">
          <div class="card-title">
            <span class="t"><el-icon><Camera /></el-icon>车牌识别（双通道）</span>
          </div>
          <div class="upload-row">
            <el-upload drag :show-file-list="false" accept="image/png,image/jpeg"
                       :before-upload="onUpload" :disabled="recognizing">
              <el-icon style="font-size: 34px; color: #93c5fd"><UploadFilled /></el-icon>
              <div style="font-size: 13px; color: var(--muted); margin-top: 6px">
                点击或拖拽上传抓拍图片（模拟硬件抓拍图）<br />
                <span style="font-size: 12px">支持 PNG / JPEG，≤10MB</span>
              </div>
            </el-upload>
          </div>
          <div class="camera-row">
            <el-select v-model="camera" style="flex: 1" size="default">
              <el-option v-for="(name, id) in CAMERAS" :key="id" :value="id" :label="`${id}（${name}）`" />
            </el-select>
            <el-button type="primary" :icon="Camera" :loading="recognizing" @click="onCapture">
              相机抓拍
            </el-button>
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
            <el-alert v-if="recog.error" :title="recog.error" type="warning" :closable="false" show-icon />
            <img v-if="recog.image_url" :src="recog.image_url" class="recog-img" alt="抓拍图片" />
          </div>
        </section>

        <!-- 车位看板 -->
        <section class="card">
          <div class="card-title">
            <span class="t"><el-icon><DataLine /></el-icon>车位看板</span>
            <el-button size="small" :icon="Setting" @click="openBoardDebug">调试</el-button>
          </div>
          <div class="board-body">
            <div v-for="a in lots" :key="a.area" class="area-row">
              <div class="area-head">
                <span class="name">{{ a.area }} 区 · {{ a.name }}</span>
                <span>空闲 <b style="color: var(--brand)">{{ a.free }}</b> / {{ a.total }}</span>
              </div>
              <el-progress :percentage="a.total ? Math.round(a.free / a.total * 100) : 0"
                           :stroke-width="12" :show-text="false"
                           :color="a.free === 0 ? '#ef4444' : a.free / a.total < 0.2 ? '#f59e0b' : '#3b82f6'" />
            </div>
            <div style="font-size: 12px; color: var(--muted); text-align: right">
              更新时间：{{ lotsUpdated }}
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
    <el-dialog v-model="addUserDialog" title="添加用户" width="380px">
      <el-input v-model="newUserName" placeholder="输入用户名" maxlength="20"
                @keyup.enter="confirmAddUser" />
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
        <el-button type="danger" plain @click="resetAll">全部复位（车位+票据+时间）</el-button>
        <el-button type="primary" @click="boardDebugDialog = false">完成</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Delete, ChatDotRound, Promotion, Loading, Camera, UploadFilled, DataLine, Setting, Refresh, User } from '@element-plus/icons-vue'
import api from './api'

const CAMERAS = { 'cam-1': 'A 区入口', 'cam-2': 'B 区入口', 'cam-3': 'C 区入口', 'cam-4': '出口' }
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

async function loadUsers() {
  const { data } = await api.get('/users')
  users.value = data.users
  if (!currentUser.value && users.value.length) {
    switchUser(users.value[0])
  }
}

async function switchUser(u) {
  currentUser.value = u
  await loadMessages()
}

async function confirmAddUser() {
  const name = newUserName.value.trim()
  if (!name) return ElMessage.warning('请输入用户名')
  try {
    const { data } = await api.post('/users', { name })
    ElMessage.success(`已添加用户「${name}」`)
    addUserDialog.value = false
    newUserName.value = ''
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
const camera = ref('cam-1')
const recognizing = ref(false)
const recog = ref(null)

async function onUpload(file) {
  recognizing.value = true
  try {
    const form = new FormData()
    form.append('file', file)
    const { data } = await api.post('/recognize', form)
    recog.value = data.result
  } catch (e) {
    ElMessage.error(e.response?.data?.error || '识别请求失败')
  } finally {
    recognizing.value = false
  }
  return false // 阻止 el-upload 默认上传
}

async function onCapture() {
  recognizing.value = true
  try {
    const { data } = await api.post('/capture', { camera_id: camera.value })
    recog.value = data.result
  } catch (e) {
    ElMessage.error(e.response?.data?.error || '抓拍失败')
  } finally {
    recognizing.value = false
  }
}

// ── 看板 ──
const lots = ref([])
const lotsUpdated = ref('—')
const health = reactive({ mock: true, virtualTime: '', realTime: '' })
const boardDebugDialog = ref(false)
const debugForm = reactive({})
const debugTime = ref(null)

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
  ElMessage.success('车位/票据/时间已全部复位')
  await loadLots()
}

onMounted(async () => {
  await loadUsers()
  await loadLots()
  setInterval(loadHealth, 15000)
})
</script>
