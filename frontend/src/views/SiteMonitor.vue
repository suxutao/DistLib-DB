<template>
  <div class="page">
    <header class="page-header">
      <h1>🖥️ Site {{ port }} — 命令处理监控</h1>
      <p class="hint">实时显示主站点下发到本节点的子查询及处理结果（不写日志文件）</p>
    </header>

    <div class="card">
      <div class="status-bar">
        <span class="dot" :class="online ? 'green' : 'red'"></span>
        <span class="status-text">{{ online ? '分站点在线' : '分站点离线' }}</span>
        <span class="ws-dot" :class="wsConnected ? 'green' : 'red'"></span>
        <span class="status-text">WebSocket {{ wsConnected ? '已连接' : '未连接' }}</span>
        <span class="counter">已处理 {{ stats.total }} 条命令</span>
        <span class="counter ok">成功 {{ stats.ok }}</span>
        <span class="counter err">失败 {{ stats.err }}</span>
      </div>

      <div class="filter-bar">
        <label><input type="checkbox" v-model="filterIncoming" /> 接收</label>
        <label><input type="checkbox" v-model="filterOutgoing" /> 返回</label>
        <label><input type="checkbox" v-model="filterError" /> 错误</label>
        <button class="btn" @click="logs = []">🗑️ 清空</button>
      </div>

      <div class="log" ref="logRef">
        <div v-for="(m, i) in filteredLogs" :key="i" :class="['log-line', m.direction]">
          <span class="ts">[{{ m.timestamp }}]</span>
          <span :class="['icon', m.direction]">{{ iconOf(m.direction) }}</span>
          <pre class="content">{{ m.content }}</pre>
        </div>
        <div v-if="filteredLogs.length === 0" class="empty">
          等待命令中...（请先启动 port={{ port }} 的分站点后端）
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
defineOptions({ name: 'SiteMonitor' })
import { computed, nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const port = computed(() => route.params.port || '8001')

const API_BASE = computed(() => `http://localhost:${port.value}`)
const WS_URL = computed(() => `ws://localhost:${port.value}/ws/commands`)

const online = ref(false)
const wsConnected = ref(false)
const logs = ref([])
const logRef = ref(null)
const stats = ref({ total: 0, ok: 0, err: 0 })

const filterIncoming = ref(true)
const filterOutgoing = ref(true)
const filterError = ref(true)

const filteredLogs = computed(() => {
  return logs.value.filter((m) => {
    if (m.direction === 'incoming' && filterIncoming.value) return true
    if (m.direction === 'outgoing' && filterOutgoing.value) return true
    if (m.direction === 'error' && filterError.value) return true
    if (m.direction === 'info') return true
    return false
  })
})

function iconOf(dir) {
  return { incoming: '⬇️', outgoing: '⬆️', error: '❌', info: 'ℹ️' }[dir] || '•'
}

function scrollLogToBottom() {
  nextTick(() => {
    if (logRef.value) logRef.value.scrollTop = logRef.value.scrollHeight
  })
}

let ws = null
let wsRetryTimer = null
let siteTimer = null

function closeWS() {
  if (ws) { ws.close(); ws = null }
  if (wsRetryTimer) { clearTimeout(wsRetryTimer); wsRetryTimer = null }
  wsConnected.value = false
}

function connectWS() {
  closeWS()
  try {
    ws = new WebSocket(WS_URL.value)
    ws.onopen = () => { wsConnected.value = true }
    ws.onclose = () => {
      wsConnected.value = false
      wsRetryTimer = setTimeout(connectWS, 2000)
    }
    ws.onerror = () => { wsConnected.value = false }
    ws.onmessage = (e) => {
      try {
        const m = JSON.parse(e.data)
        if (m.direction === 'incoming') stats.value.total++
        if (m.direction === 'outgoing') stats.value.ok++
        if (m.direction === 'error') stats.value.err++
        logs.value.push({
          timestamp: m.timestamp || new Date().toLocaleTimeString(),
          direction: m.direction || 'info',
          content: m.content,
        })
        if (logs.value.length > 500) logs.value.shift()
        scrollLogToBottom()
      } catch {}
    }
  } catch {}
}

async function checkHealth() {
  try {
    const resp = await fetch(API_BASE.value + '/api/health')
    online.value = resp.status === 200
  } catch {
    online.value = false
  }
}

function startTimers() {
  checkHealth()
  if (!siteTimer) siteTimer = setInterval(checkHealth, 3000)
}
function stopTimers() {
  if (siteTimer) { clearInterval(siteTimer); siteTimer = null }
}

onMounted(() => {
  connectWS()
  startTimers()
})

// keep-alive 切走时保留 WS 连接（继续接收实时数据），但暂停 health 轮询
onDeactivated(() => {
  stopTimers()
})
// keep-alive 切回：恢复轮询 + 滚到底
onActivated(() => {
  startTimers()
  scrollLogToBottom()
})
// 彻底销毁（页面关闭时）
onBeforeUnmount(() => {
  closeWS()
  stopTimers()
})

// 日志更新后自动滚到底
watch(logs, () => {
  nextTick(() => {
    if (logRef.value) logRef.value.scrollTop = logRef.value.scrollHeight
  })
}, { deep: true })
</script>

<style scoped>
.page-header { margin-bottom: 20px; }
.page-header h1 { font-size: 24px; color: #1e3a8a; }
.page-header .hint { color: #6b7280; font-size: 13px; margin-top: 4px; }

.card {
  background: #fff; border-radius: 10px; padding: 18px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}

.status-bar {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 14px; background: #f3f4f6; border-radius: 6px;
  margin-bottom: 12px; font-size: 13px;
}
.dot, .ws-dot { width: 10px; height: 10px; border-radius: 50%; }
.dot.green, .ws-dot.green { background: #22c55e; box-shadow: 0 0 6px #22c55e; }
.dot.red, .ws-dot.red { background: #ef4444; }
.status-text { color: #374151; }
.counter { margin-left: auto; color: #6b7280; background: #fff; padding: 2px 10px; border-radius: 10px; }
.counter.ok { color: #16a34a; }
.counter.err { color: #dc2626; }

.filter-bar {
  display: flex; gap: 14px; align-items: center; margin-bottom: 12px; font-size: 13px;
}
.filter-bar label { display: flex; gap: 4px; align-items: center; cursor: pointer; }
.btn {
  margin-left: auto; padding: 6px 14px; border-radius: 6px; border: 1px solid #d1d5db;
  background: #fff; cursor: pointer; font-size: 13px;
}

.log {
  background: #0f172a; color: #e2e8f0;
  border-radius: 6px; padding: 14px; height: calc(100vh - 260px); overflow-y: auto;
  font-family: "Cascadia Code", Consolas, monospace; font-size: 13px;
}
.log-line { margin-bottom: 10px; display: flex; gap: 6px; align-items: flex-start; }
.log-line .ts { color: #64748b; flex-shrink: 0; }
.log-line .icon { flex-shrink: 0; }
.log-line.incoming .icon { color: #60a5fa; }
.log-line.outgoing .icon { color: #4ade80; }
.log-line.error .icon { color: #f87171; }
.log-line.info .icon { color: #a78bfa; }
.log-line .content {
  flex: 1; white-space: pre-wrap; word-break: break-all;
  background: #1e293b; padding: 6px 10px; border-radius: 4px;
  margin-top: 2px;
}
.log-line.incoming .content { border-left: 2px solid #60a5fa; }
.log-line.outgoing .content { border-left: 2px solid #4ade80; }
.log-line.error .content { border-left: 2px solid #f87171; color: #fecaca; }
.empty { text-align: center; color: #64748b; padding: 40px; }
</style>
