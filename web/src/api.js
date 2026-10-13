import axios from 'axios'

const api = axios.create({ baseURL: '/api', timeout: 180000 })

export default api
