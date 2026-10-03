<script setup lang="ts">
import { useRoute } from 'vue-router'

const route = useRoute()
</script>

<template>
  <header class="at-header">
    <div class="at-header-inner">
      <div class="brand" @click="$router.push('/projects')">
        <span class="logo-mark">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
            <path d="M4 5.5C4 4.7 4.7 4 5.5 4h13c.8 0 1.5.7 1.5 1.5v9c0 .8-.7 1.5-1.5 1.5H13l-4.2 3.7c-.5.4-1.3.1-1.3-.6V16H5.5C4.7 16 4 15.3 4 14.5v-9z" fill="#fff"/>
            <rect x="7.2" y="7.6" width="6.4" height="1.7" rx=".85" fill="#6366f1"/>
            <rect x="7.2" y="10.6" width="9.6" height="1.7" rx=".85" fill="#c7c9f9"/>
          </svg>
        </span>
        <span class="brand-name">AnimeTranslation</span>
        <span class="brand-sub">AI 动漫番剧翻译平台</span>
      </div>
      <el-menu mode="horizontal" router :default-active="route.path" class="at-nav" ellipsis>
        <el-menu-item index="/projects">番剧项目</el-menu-item>
        <el-menu-item index="/glossary">术语表</el-menu-item>
        <el-menu-item index="/channels">翻译渠道</el-menu-item>
        <el-menu-item index="/settings">系统设置</el-menu-item>
      </el-menu>
    </div>
  </header>

  <el-main class="at-main">
    <router-view />
  </el-main>

  <footer class="at-footer">AnimeTranslation · AI 提效，人把质量关 · M1 v0.1</footer>
</template>

<style scoped>
/* 结构样式在 global.css */
</style>
