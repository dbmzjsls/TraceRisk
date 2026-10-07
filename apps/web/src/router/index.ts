import { createRouter, createWebHistory } from 'vue-router'
import type { Role } from '../api/schemas'
import { useAuthStore } from '../features/auth/store'
import { refreshSession, safeRedirect } from '../features/auth/useAuth'
import { ApiError } from '../api/client'
import WorkspaceLayout from '../layouts/WorkspaceLayout.vue'
import LoginPage from '../features/auth/LoginPage.vue'
import ErrorPage from '../pages/ErrorPage.vue'
import CompanyListPage from '../features/companies/CompanyListPage.vue'
import CompanyDetailPage from '../features/companies/CompanyDetailPage.vue'
import InvestigationListPage from '../features/investigations/InvestigationListPage.vue'
import InvestigationDetailPage from '../features/investigations/InvestigationDetailPage.vue'
import ReviewListPage from '../features/reviews/ReviewListPage.vue'
import ReviewDetailPage from '../features/reviews/ReviewDetailPage.vue'
import ReportPage from '../features/reports/ReportPage.vue'

declare module 'vue-router' { interface RouteMeta { public?: boolean; roles?: Role[] } }
export const router = createRouter({ history: createWebHistory(), routes: [
  { path: '/login', component: LoginPage, meta: { public: true } },
  { path: '/403', component: ErrorPage, props: { status: '403' } },
  { path: '/unavailable', component: ErrorPage, props: { status: '500' }, meta: { public: true } },
  { path: '/', component: WorkspaceLayout, children: [
    { path: '', redirect: '/companies' },
    { path: 'companies', component: CompanyListPage }, { path: 'companies/:id', component: CompanyDetailPage },
    { path: 'investigations', component: InvestigationListPage }, { path: 'investigations/:id', component: InvestigationDetailPage },
    { path: 'reviews', component: ReviewListPage, meta: { roles: ['reviewer', 'admin'] } }, { path: 'reviews/:id', component: ReviewDetailPage, meta: { roles: ['reviewer', 'admin'] } },
    { path: 'reports/:id', component: ReportPage },
  ] },
  { path: '/:pathMatch(.*)*', component: ErrorPage, meta: { public: true } },
] })
let verifiedToken: string | null = null
router.beforeEach(async to => {
  const auth = useAuthStore()
  if (auth.token && verifiedToken !== auth.token && to.path !== '/unavailable') {
    try { await refreshSession(); verifiedToken = auth.token } catch (error) {
      if (error instanceof ApiError && error.status === 401) verifiedToken = null
      else return '/unavailable'
    }
  }
  if (to.path === '/login' && auth.token && auth.user) return safeRedirect(to.query.redirect)
  if (!to.meta.public && (!auth.token || !auth.user)) return { path: '/login', query: { redirect: to.fullPath } }
  if (to.meta.roles && (!auth.role || !to.meta.roles.includes(auth.role))) return '/403'
})
