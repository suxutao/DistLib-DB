import { createRouter, createWebHistory } from 'vue-router'
import MainSite from '../views/MainSite.vue'
import SiteMonitor from '../views/SiteMonitor.vue'

const routes = [
  { path: '/', name: 'main', component: MainSite },
  { path: '/site/:port', name: 'site', component: SiteMonitor, props: true },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
