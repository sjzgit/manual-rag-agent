import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'chat', component: () => import('../views/ChatView.vue') },
    {
      path: '/admin',
      component: () => import('../views/admin/AdminLayout.vue'),
      children: [
        { path: '', redirect: '/admin/knowledge' },
        { path: 'knowledge', component: () => import('../views/admin/KnowledgeManage.vue') },
        { path: 'prompt', component: () => import('../views/admin/PromptManage.vue') },
        { path: 'session', component: () => import('../views/admin/SessionManage.vue') },
        { path: 'feedback', component: () => import('../views/admin/FeedbackManage.vue') },
      ],
    },
  ],
})

export default router
