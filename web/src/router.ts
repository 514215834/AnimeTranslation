import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/projects' },
    { path: '/projects', name: 'projects', component: () => import('./views/ProjectList.vue'), meta: { title: '番剧项目' } },
    { path: '/projects/:id', name: 'project', component: () => import('./views/ProjectDetail.vue'), meta: { title: '项目详情' } },
    { path: '/episodes/:id', name: 'episode', component: () => import('./views/EpisodeDetail.vue'), meta: { title: '剧集详情' } },
    { path: '/glossary', name: 'glossary', component: () => import('./views/Glossary.vue'), meta: { title: '术语表' } },
    { path: '/channels', name: 'channels', component: () => import('./views/Channels.vue'), meta: { title: '翻译渠道' } },
    { path: '/settings', name: 'settings', component: () => import('./views/Settings.vue'), meta: { title: '系统设置' } },
  ],
})

router.afterEach((to) => {
  document.title = `${to.meta.title ?? ''} · AnimeTranslation`
})

export default router
