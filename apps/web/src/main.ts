import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { VueQueryPlugin } from '@tanstack/vue-query'
import App from './App.vue'
import { router } from './router'
import { configureAuth, queryClient } from './api/client'
import { useAuthStore } from './features/auth/store'
import { clearSession } from './features/auth/useAuth'
import 'virtual:uno.css'
import './styles.css'

const app = createApp(App)
app.use(createPinia())
const auth = useAuthStore()
auth.restore()
configureAuth(() => auth.token, () => {
  clearSession()
  if (router.currentRoute.value.path !== '/login') void router.replace({ path: '/login', query: { redirect: router.currentRoute.value.fullPath } })
})
app.use(VueQueryPlugin, { queryClient })
app.use(router)
app.mount('#app')
