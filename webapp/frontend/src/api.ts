import axios from 'axios'
import router from './router'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '/library/api',
  withCredentials: true,
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response && error.response.status === 401) {
      // Public routes that don't require an active session
      const publicRoutes = ['login', 'register', 'set-password']

      // Redirect to login if unauthorized AND not already on a public page
      if (router.currentRoute.value.name && !publicRoutes.includes(router.currentRoute.value.name.toString())) {
        router.push({ name: 'login' })
      }
    }
    return Promise.reject(error)
  }
)

export default api
