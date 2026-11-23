import { createRouter, createWebHistory } from 'vue-router'

import OffsetLedgerView from '@/views/OffsetLedgerView.vue'
import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    // 固定路径必须排在 /:moduleKey 之前，否则会被通用工作台路由抢先匹配。
    { path: '/offset-ledger', name: 'offset-ledger', component: OffsetLedgerView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
