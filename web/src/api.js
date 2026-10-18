import axios from 'axios'

// axios 实例：统一 /api 前缀（生产由 8100 静态托管；开发由 vite 代理到 8100）。
// 超时 180s：识别与对话链路含在线模型调用（视觉 API 可达数秒～数十秒）。
const api = axios.create({ baseURL: '/api', timeout: 180000 })

export default api
