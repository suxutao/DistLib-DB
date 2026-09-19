<template>
  <div class="page">
    <header class="page-header">
      <h1>🧭 主站点 · 查询控制台</h1>
      <p class="hint">输入 SQL，主站点将自动分解并下发到各分站点执行</p>
    </header>

    <!-- Tab 切换 -->
    <div class="tabs">
      <button
        v-for="t in tabs"
        :key="t.key"
        class="tab"
        :class="{ active: activeTab === t.key }"
        @click="activeTab = t.key"
      >
        {{ t.label }}
      </button>
    </div>

    <!-- Tab 1: 查询控制台 -->
    <div v-if="activeTab === 'console'" class="tab-panel">
      <div class="card">
        <div class="card-title">SQL 查询输入</div>
        <textarea
          v-model="sqlInput"
          class="sql-editor"
          placeholder="SELECT * FROM books WHERE category IN ('文学', '科技')"
          spellcheck="false"
        ></textarea>
        <div class="toolbar">
          <button class="btn primary" :disabled="running" @click="runQuery">
            {{ running ? '⏳ 执行中...' : '▶ 执行查询' }}
          </button>
          <button class="btn" @click="reset">🔄 重置</button>
          <div class="toolbar-right">
            <select v-model="preset" class="preset-select" @change="sqlInput = preset">
              <option value="">📌 示例查询</option>
              <option v-for="p in presets" :key="p" :value="p">{{ p }}</option>
            </select>
          </div>
        </div>
      </div>

      <div class="card result-card">
        <div class="card-title">
          查询结果
          <span v-if="result?.partial" class="badge warn">⚠ 部分站点未响应</span>
          <span v-if="result?.warnings?.length" class="badge warn">{{ result.warnings[0] }}</span>
          <span v-if="result?.success" class="count">共 {{ result.rows?.length ?? 0 }} 行</span>
        </div>
        <div v-if="error" class="error-box">❌ {{ error }}</div>
        <div v-else-if="!result" class="empty">请先执行一条查询</div>
        <div v-else-if="result.rows?.length === 0" class="empty">查询无匹配结果</div>
        <div v-else class="table-wrap">
          <table class="result-table">
            <thead>
              <tr>
                <th>#</th>
                <th v-for="c in result.columns" :key="c">{{ c }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, idx) in pagedRows" :key="idx">
                <td class="idx">{{ (currentPage - 1) * pageSize + idx + 1 }}</td>
                <td v-for="(v, j) in row" :key="j">{{ v }}</td>
              </tr>
            </tbody>
          </table>
          <div v-if="totalRows > pageSize" class="pagination">
            <button class="page-btn" :disabled="currentPage === 1" @click="currentPage = 1">«</button>
            <button class="page-btn" :disabled="currentPage === 1" @click="currentPage--">‹</button>
            <template v-for="p in pageNumbers" :key="p">
              <span v-if="p === '...'" class="page-ellipsis">...</span>
              <button v-else class="page-btn" :class="{ active: p === currentPage }" @click="currentPage = p">{{ p }}</button>
            </template>
            <button class="page-btn" :disabled="currentPage === totalPages" @click="currentPage++">›</button>
            <button class="page-btn" :disabled="currentPage === totalPages" @click="currentPage = totalPages">»</button>
            <span class="page-info">共 {{ totalRows }} 条</span>
            <select v-model="pageSize" class="page-size">
              <option :value="10">10 / 页</option>
              <option :value="20">20 / 页</option>
              <option :value="50">50 / 页</option>
              <option :value="100">100 / 页</option>
            </select>
          </div>
        </div>
      </div>
    </div>

    <!-- Tab 2: 分解过程 -->
    <div v-if="activeTab === 'plan'" class="tab-panel">
      <div class="card">
        <div class="card-title">🔍 查询分解与优化过程（实时）</div>
        <div class="log" ref="logRef">
          <div v-for="(m, i) in planLog" :key="i" class="log-line">
            <span class="ts">[{{ m.timestamp }}]</span>
            <span :class="['log-type', m.type]">{{ typeLabel(m.type) }}</span>
            <span class="log-content">{{ m.content }}</span>
          </div>
          <div v-if="planLog.length === 0" class="empty small">暂无日志，执行一条查询开始</div>
        </div>
      </div>

      <div v-if="result?.plan" class="card">
        <div class="card-title">📋 分解计划详情</div>
        <div class="plan-grid">
          <div>
            <h4>原始 SQL</h4>
            <pre class="code">{{ result.plan.original_sql }}</pre>
          </div>
          <div>
            <h4>涉及站点</h4>
            <ul class="site-list">
              <li v-for="s in result.plan.involved_sites" :key="s">{{ s }}</li>
            </ul>
          </div>
        </div>
        <h4>子查询</h4>
        <div v-for="sq in result.plan.sub_queries" :key="sq.site" class="subq">
          <div class="subq-site">→ {{ sq.site }}</div>
          <pre class="code">{{ sq.sql }}</pre>
        </div>
        <div v-if="result.plan.notes?.length">
          <h4>优化动作</h4>
          <ul>
            <li v-for="n in result.plan.notes" :key="n">✨ {{ n }}</li>
          </ul>
        </div>
      </div>
    </div>

    <!-- Tab 3: 分站点监控 -->
    <div v-if="activeTab === 'monitor'" class="tab-panel">
      <div class="card">
        <div class="card-title">🖥️ 分站点实时状态</div>
        <div class="monitor-grid">
          <div
            v-for="s in sites"
            :key="s.id"
            class="site-card"
            :class="{ offline: !s.online }"
          >
            <div class="site-head">
              <span class="dot" :class="s.online ? 'green' : 'red'"></span>
              <span class="site-id">{{ s.id }}</span>
              <span class="site-cat">{{ s.category }}</span>
            </div>
            <div class="site-body">
              <div class="site-row">状态：<b>{{ s.online ? '在线' : '离线' }}</b></div>
              <div class="site-row">URL：<code>{{ s.url }}</code></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
defineOptions({ name: 'MainSite' })
import { onActivated, onBeforeUnmount, onDeactivated, onMounted, computed, nextTick, ref, watch } from 'vue'

const API = 'http://localhost:8000'
const WS_URL = 'ws://localhost:8000/ws/monitor'

const tabs = [
  { key: 'console', label: '1️⃣ 查询控制台' },
  { key: 'plan', label: '2️⃣ 分解过程' },
  { key: 'monitor', label: '3️⃣ 站点监控' },
]
const activeTab = ref('console')

const sqlInput = ref("SELECT * FROM books WHERE category IN ('文学', '科技')")
const running = ref(false)
const result = ref(null)
const error = ref('')
const preset = ref('')

const presets = [
  "SELECT * FROM books",
  "SELECT title, author, total_count FROM books WHERE category = '文学'",
  "SELECT * FROM books WHERE category IN ('文学', '历史') AND total_count > 2",
  "SELECT * FROM borrow_records WHERE book_id = 1",
  "SELECT * FROM readers",
  "SELECT title, author FROM books WHERE author LIKE '%马%'",
  "SELECT title, category FROM books WHERE total_count >= 4",
  // --- 高级查询 ---
  "SELECT category, COUNT(*) FROM books GROUP BY category",
  "SELECT category, SUM(total_count), MIN(total_count), MAX(total_count) FROM books GROUP BY category",
  "SELECT reader_name, COUNT(*) FROM borrow_records GROUP BY reader_name ORDER BY COUNT(*) DESC",
  "SELECT title, category, total_count FROM books ORDER BY total_count DESC LIMIT 5",
  "SELECT DISTINCT author FROM books",
  "SELECT * FROM books LIMIT 10 OFFSET 5",
]

const planLog = ref([])
const logRef = ref(null)
let ws = null

const sites = ref([])

// ---- 分页 ----
const currentPage = ref(1)
const pageSize = ref(10)
const totalRows = computed(() => result.value?.rows?.length ?? 0)
const totalPages = computed(() => Math.max(1, Math.ceil(totalRows.value / pageSize.value)))
const pagedRows = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return result.value?.rows?.slice(start, start + pageSize.value) ?? []
})
const pageNumbers = computed(() => {
  const total = totalPages.value
  const cur = currentPage.value
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1)
  const nums = []
  nums.push(1)
  if (cur > 3) nums.push('...')
  for (let i = Math.max(2, cur - 1); i <= Math.min(total - 1, cur + 1); i++) nums.push(i)
  if (cur < total - 2) nums.push('...')
  nums.push(total)
  return nums
})
watch(totalRows, () => { currentPage.value = 1 })

// 滚到底的辅助函数
function scrollLogToBottom() {
  nextTick(() => {
    if (logRef.value) logRef.value.scrollTop = logRef.value.scrollHeight
  })
}

// WebSocket 实时追加日志
function connectWS() {
  closeWS()
  ws = new WebSocket(WS_URL)
  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data)
      planLog.value.push({
        timestamp: msg.timestamp || new Date().toLocaleTimeString(),
        type: msg.type,
        content: msg.content,
      })
      if (planLog.value.length > 500) planLog.value.shift()
      scrollLogToBottom()  // 收到就滚（DOM 在的话立即生效）
    } catch {}
  }
  ws.onclose = () => { wsRetryTimer = setTimeout(connectWS, 2000) }
}

async function runQuery() {
  if (!sqlInput.value.trim() || running.value) return
  running.value = true
  error.value = ''
  result.value = null
  // 注意：不再清空 planLog，让实时日志持续积累
  try {
    const resp = await fetch(API + '/api/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sql: sqlInput.value }),
    })
    const data = await resp.json()
    result.value = data
    if (!data.success && !data.plan) {
      error.value = data.error || '执行失败'
    }
  } catch (e) {
    error.value = '网络错误: ' + e.message
  } finally {
    running.value = false
  }
}

function reset() {
  sqlInput.value = ''
  result.value = null
  error.value = ''
  preset.value = ''
  planLog.value = []
}

// 切到日志 Tab 时滚到底（解决 Tab 没激活时 push 的日志无法触发滚动的问题）
watch(activeTab, (tab) => {
  if (tab === 'plan') scrollLogToBottom()
})
// keep-alive 切回来也滚
watch(planLog, scrollLogToBottom, { deep: true })

function typeLabel(t) {
  return { plan_step: '📌', command: '🔧', info: 'ℹ️' }[t] || '•'
}

async function refreshSites() {
  try {
    const resp = await fetch(API + '/api/sites')
    const data = await resp.json()
    sites.value = data.sites || []
  } catch {}
}

let siteTimer = null
let wsRetryTimer = null

function closeWS() {
  if (ws) { ws.close(); ws = null }
  if (wsRetryTimer) { clearTimeout(wsRetryTimer); wsRetryTimer = null }
}

onMounted(() => {
  connectWS()
  refreshSites()
  siteTimer = setInterval(refreshSites, 5000)
})
// keep-alive 切走：保留 WS，暂停轮询
onDeactivated(() => {
  if (siteTimer) { clearInterval(siteTimer); siteTimer = null }
})
// keep-alive 切回：恢复轮询
onActivated(() => {
  refreshSites()
  if (!siteTimer) siteTimer = setInterval(refreshSites, 5000)
})
// 页面关闭时彻底清理
onBeforeUnmount(() => {
  closeWS()
  if (siteTimer) clearInterval(siteTimer)
})
</script>

<style scoped>
.page-header { margin-bottom: 20px; }
.page-header h1 { font-size: 24px; color: #1e3a8a; }
.page-header .hint { color: #6b7280; font-size: 13px; margin-top: 4px; }

.tabs {
  display: flex; gap: 0; margin-bottom: 16px;
  border-bottom: 2px solid #e5e7eb;
}
.tab {
  padding: 10px 18px;
  background: none; border: none; cursor: pointer;
  font-size: 14px; font-weight: 500; color: #6b7280;
  border-bottom: 2px solid transparent; margin-bottom: -2px;
  transition: color 0.2s;
}
.tab:hover { color: #1e3a8a; }
.tab.active { color: #1e3a8a; border-bottom-color: #1e3a8a; }

.tab-panel { display: flex; flex-direction: column; gap: 16px; }

.card {
  background: #fff; border-radius: 10px; padding: 18px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}
.card-title {
  font-weight: 600; font-size: 15px; color: #1f2937;
  margin-bottom: 14px; display: flex; align-items: center; gap: 12px;
}
.badge {
  font-size: 12px; padding: 2px 8px; border-radius: 4px;
}
.badge.warn { background: #fef3c7; color: #92400e; }
.count { margin-left: auto; font-weight: 400; color: #6b7280; font-size: 13px; }

.sql-editor {
  width: 100%; height: 120px; padding: 12px;
  border: 1px solid #d1d5db; border-radius: 6px;
  font-family: "Cascadia Code", Consolas, monospace; font-size: 14px;
  resize: vertical;
}
.sql-editor:focus { outline: none; border-color: #1e3a8a; }

.toolbar {
  margin-top: 12px; display: flex; gap: 10px; align-items: center;
}
.btn {
  padding: 8px 18px; border-radius: 6px; border: 1px solid #d1d5db;
  background: #fff; cursor: pointer; font-size: 14px;
  transition: all 0.2s;
}
.btn:hover { border-color: #1e3a8a; color: #1e3a8a; }
.btn.primary { background: #1e3a8a; color: #fff; border-color: #1e3a8a; }
.btn.primary:hover { background: #1e40af; color: #fff; }
.btn:disabled { opacity: 0.6; cursor: not-allowed; }
.toolbar-right { margin-left: auto; }
.preset-select {
  padding: 8px 12px; border: 1px solid #d1d5db; border-radius: 6px;
  background: #fff; font-size: 13px; cursor: pointer;
}

.error-box {
  background: #fef2f2; color: #dc2626; border: 1px solid #fecaca;
  padding: 12px; border-radius: 6px; font-size: 14px;
}
.empty {
  padding: 40px; text-align: center; color: #9ca3af;
}
.empty.small { padding: 20px; }

.table-wrap { overflow-x: auto; }

.pagination {
  display: flex; align-items: center; gap: 6px;
  padding: 14px 4px 0; margin-top: 12px;
  border-top: 1px solid #e5e7eb;
  flex-wrap: wrap;
}
.page-btn {
  min-width: 32px; height: 32px; padding: 0 8px;
  border: 1px solid #d1d5db; border-radius: 4px;
  background: #fff; cursor: pointer; font-size: 13px;
  color: #374151; transition: all 0.15s;
}
.page-btn:hover:not(:disabled) { border-color: #1e3a8a; color: #1e3a8a; }
.page-btn.active { background: #1e3a8a; color: #fff; border-color: #1e3a8a; }
.page-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.page-ellipsis { color: #9ca3af; padding: 0 4px; font-size: 13px; }
.page-info { margin-left: 10px; color: #6b7280; font-size: 13px; }
.page-size {
  margin-left: auto; padding: 6px 10px; border: 1px solid #d1d5db;
  border-radius: 4px; background: #fff; font-size: 13px; cursor: pointer;
}
.result-table {
  width: 100%; border-collapse: collapse; font-size: 13px;
}
.result-table th, .result-table td {
  padding: 10px 12px; text-align: left; border-bottom: 1px solid #e5e7eb;
}
.result-table th { background: #f9fafb; font-weight: 600; color: #374151; }
.result-table tr:hover td { background: #f9fafb; }
.result-table .idx { color: #9ca3af; width: 40px; text-align: center; }

.log {
  background: #0f172a; color: #e2e8f0;
  border-radius: 6px; padding: 14px; height: 360px; overflow-y: auto;
  font-family: "Cascadia Code", Consolas, monospace; font-size: 13px;
}
.log-line { margin-bottom: 4px; white-space: pre-wrap; word-break: break-all; }
.log-line .ts { color: #64748b; margin-right: 6px; }
.log-line .log-type { color: #60a5fa; margin-right: 6px; }
.log-line .log-content { color: #cbd5e1; }

.plan-grid { display: grid; grid-template-columns: 2fr 1fr; gap: 20px; margin-bottom: 14px; }
.plan-grid h4, .plan-grid h4 { margin-bottom: 6px; font-size: 13px; color: #374151; }
.code {
  background: #f3f4f6; padding: 10px; border-radius: 4px;
  font-family: "Cascadia Code", Consolas, monospace; font-size: 13px;
  white-space: pre-wrap; word-break: break-all;
}
.site-list { list-style: none; }
.site-list li { padding: 4px 0; font-size: 14px; color: #374151; }

.subq {
  background: #f9fafb; border-left: 3px solid #1e3a8a;
  padding: 10px 14px; margin-bottom: 10px; border-radius: 0 6px 6px 0;
}
.subq-site { font-weight: 600; color: #1e3a8a; margin-bottom: 6px; font-size: 13px; }

.monitor-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 14px;
}
.site-card {
  background: #f9fafb; border: 1px solid #e5e7eb;
  border-radius: 8px; padding: 14px; transition: all 0.2s;
}
.site-card.offline { opacity: 0.6; background: #fef2f2; border-color: #fecaca; }
.site-head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.dot { width: 10px; height: 10px; border-radius: 50%; }
.dot.green { background: #22c55e; box-shadow: 0 0 6px #22c55e; }
.dot.red { background: #ef4444; }
.site-id { font-weight: 600; color: #1f2937; }
.site-cat { margin-left: auto; font-size: 12px; background: #e0e7ff; color: #3730a3; padding: 2px 8px; border-radius: 10px; }
.site-card.offline .site-cat { background: #fecaca; color: #991b1b; }
.site-row { font-size: 13px; color: #374151; padding: 2px 0; }
.site-row code { font-size: 12px; color: #6b7280; }
</style>
