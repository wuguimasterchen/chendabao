import axios from 'axios'

// 创建Axios实例，配置后端基础地址
const request = axios.create({
  baseURL: 'http://43.138.21.195:8002',  // 后端公网IP+端口
  timeout: 5000  // 请求超时时间
})

// 导出实例，供其他组件使用
export default request