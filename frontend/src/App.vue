<template>
  <div class="app">
    <aside class="sidebar">
      <div class="brand">DistLib-DB</div>
      <div class="subtitle">分布式图书馆</div>
      <nav>
        <router-link to="/" class="nav-item" exact-active-class="active">
          <span class="nav-icon">🏠</span> 主站点
        </router-link>
        <router-link to="/site/8001" class="nav-item" active-class="active">
          <span class="nav-icon">🖥️</span> Site A · 文学
        </router-link>
        <router-link to="/site/8002" class="nav-item" active-class="active">
          <span class="nav-icon">🖥️</span> Site B · 科技
        </router-link>
        <router-link to="/site/8003" class="nav-item" active-class="active">
          <span class="nav-icon">🖥️</span> Site C · 教育
        </router-link>
        <router-link to="/site/8004" class="nav-item" active-class="active">
          <span class="nav-icon">🖥️</span> Site D · 历史
        </router-link>
      </nav>
    </aside>
    <main class="content">
      <router-view v-slot="{ Component, route }">
        <keep-alive>
          <component :is="Component" :key="route.fullPath" />
        </keep-alive>
      </router-view>
    </main>
  </div>
</template>

<script setup></script>

<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body, #app { height: 100%; font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; }
body { background: #f5f7fa; color: #1f2937; }

.app { display: flex; height: 100vh; }

.sidebar {
  width: 220px;
  flex-shrink: 0;
  background: linear-gradient(180deg, #1e3a8a 0%, #1e40af 100%);
  color: #e0e7ff;
  padding: 24px 16px;
  display: flex;
  flex-direction: column;
}
.brand {
  font-size: 22px;
  font-weight: 700;
  letter-spacing: 1px;
  color: #fff;
}
.subtitle {
  font-size: 12px;
  color: #bfdbfe;
  margin-bottom: 24px;
}
nav { display: flex; flex-direction: column; gap: 4px; }
.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  color: #dbeafe;
  text-decoration: none;
  font-size: 14px;
  transition: background 0.2s;
}
.nav-item:hover { background: rgba(255,255,255,0.1); color: #fff; }
.nav-item.active { background: rgba(255,255,255,0.2); color: #fff; font-weight: 600; }
.nav-icon { font-size: 16px; }

.content { flex: 1; overflow-y: auto; padding: 24px; }
</style>
