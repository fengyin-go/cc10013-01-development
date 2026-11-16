<template>
  <section class="page" data-module="admission">
    <header class="page-head">
      <div>
        <h2>端点接入清单</h2>
        <p class="page-desc">
          对多选采集端点执行批量统一准入：按规范化接口地址去重，认证信息不全单独退回，
          准入回执与隔离原因同表可见，结果回写接口管理待办区。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" :disabled="!selectedIds.length" @click="runBatchAdmit">
          批量准入（{{ selectedIds.length }}）
        </button>
        <button class="btn" type="button" @click="exportRows">导出接入清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <h3 class="block-title">① 采集端点配置（多选）</h3>
    <form class="filter-bar" @submit.prevent="reloadCollect">
      <label class="filter-item">
        <span>端点编号</span>
        <input v-model="collectFilter.keyword" placeholder="按端点编号检索，如 EP-201" />
      </label>
      <label class="filter-item">
        <span>迁移批次</span>
        <input v-model="collectFilter.batch" placeholder="按迁移批次过滤" />
      </label>
      <label class="filter-item">
        <span>处理状态</span>
        <select v-model="collectFilter.status">
          <option value="">全部</option>
          <option v-for="s in collectStatuses" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetCollectFilter">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th class="col-check">
            <input
              type="checkbox"
              :checked="allChecked"
              :disabled="!selectableRows.length"
              @change="toggleAll"
            />
          </th>
          <th v-for="column in collectColumns" :key="column">{{ column }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in collectRows" :key="String(row.id)" :class="{ 'row-disabled': !isSelectable(row) }">
          <td class="col-check">
            <input
              type="checkbox"
              :value="row.id"
              v-model="selectedIds"
              :disabled="!isSelectable(row)"
            />
          </td>
          <td v-for="column in collectColumns" :key="column">
            <span v-if="column === '处理状态'">
              <em class="badge" :class="badgeClass(row[column])">{{ row[column] ?? '—' }}</em>
            </span>
            <span v-else-if="column === '认证模板'">
              {{ row[column] || '（模板缺失）' }}
            </span>
            <span v-else>{{ row[column] ?? '—' }}</span>
          </td>
        </tr>
        <tr v-if="!collectRows.length">
          <td :colspan="collectColumns.length + 1" class="empty-state">暂无符合条件的采集端点配置</td>
        </tr>
      </tbody>
    </table>

    <p v-if="resultMessage" class="batch-msg" :class="{ 'msg-error': !lastOk }">{{ resultMessage }}</p>

    <h3 class="block-title">② 端点接入清单数据集（准入回执 + 隔离原因）</h3>
    <form class="filter-bar" @submit.prevent="reloadAdmissions">
      <label class="filter-item">
        <span>端点编号</span>
        <input v-model="admissionFilter.keyword" placeholder="按端点编号核对，如 EP-201" />
      </label>
      <label class="filter-item">
        <span>准入状态</span>
        <select v-model="admissionFilter.status">
          <option value="">全部</option>
          <option v-for="s in admissionStatuses" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>迁移批次</span>
        <input v-model="admissionFilter.batch" placeholder="按迁移批次过滤" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetAdmissionFilter">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in admissionColumns" :key="column">{{ column }}</th>
          <th>裁决操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in admissionRows" :key="String(row.id)">
          <td v-for="column in admissionColumns" :key="column">
            <span v-if="column === '准入状态'">
              <em class="badge" :class="badgeClass(row[column])">{{ row[column] ?? '—' }}</em>
            </span>
            <span v-else-if="column === '准入回执'">
              {{ row[column] || '—' }}
            </span>
            <span v-else-if="column === '隔离原因'" :class="{ 'cell-warn': row[column] }">
              {{ row[column] || '—' }}
            </span>
            <span v-else>{{ row[column] ?? '—' }}</span>
          </td>
          <td class="row-actions">
            <template v-if="row['准入状态'] === '待裁决'">
              <button class="link" type="button" @click="resolve(row, 'admit')">采纳模板接入</button>
              <button class="link link-danger" type="button" @click="resolve(row, 'reject')">退回隔离</button>
            </template>
            <span v-else class="muted-text">—</span>
          </td>
        </tr>
        <tr v-if="!admissionRows.length">
          <td :colspan="admissionColumns.length + 1" class="empty-state">
            暂无接入记录，请先在上方多选采集端点并执行批量准入
          </td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ admissionTotal }} 条准入记录 · 已选 {{ selectedIds.length }} 个待处理采集端点</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null | string[]>

const BASE = '/api/admission'
const collectColumns = [
  '端点编号', '接口地址', '请求方法', '认证模板', '认证类型',
  '认证请求头', '认证密钥', '迁移批次', '处理状态',
]
const admissionColumns = [
  '端点编号', '规范化接口地址', '请求方法', '迁移批次', '认证模板',
  '准入状态', '准入回执', '隔离原因', '处理时间',
]
const collectStatuses = ['未处理', '已处理', '已跳过']
const admissionStatuses = ['已接入', '待裁决', '已隔离']

const collectRows = ref<Row[]>([])
const admissionRows = ref<Row[]>([])
const admissionTotal = ref(0)
const selectedIds = ref<number[]>([])
const errorMessage = ref('')
const resultMessage = ref('')
const lastOk = ref(true)

const collectFilter = ref<Record<string, string>>({ keyword: '', status: '', batch: '' })
const admissionFilter = ref<Record<string, string>>({ keyword: '', status: '', batch: '' })

const selectableRows = computed(() => collectRows.value.filter((row) => isSelectable(row)))
const allChecked = computed(
  () => selectableRows.value.length > 0
    && selectableRows.value.every((row) => selectedIds.value.includes(Number(row.id))),
)

const stats = computed(() => {
  const admitted = admissionRows.value.filter((r) => r['准入状态'] === '已接入').length
  const quarantined = admissionRows.value.filter((r) => r['准入状态'] === '已隔离').length
  const conflict = admissionRows.value.filter((r) => r['准入状态'] === '待裁决').length
  const pendingCollect = collectRows.value.filter((r) => r['处理状态'] === '未处理').length
  return [
    { label: '待处理采集端点', value: pendingCollect },
    { label: '已接入', value: admitted },
    { label: '隔离退回', value: quarantined },
    { label: '冲突待裁决', value: conflict },
  ]
})

function isSelectable(row: Row): boolean {
  return row['处理状态'] === '未处理'
}

function badgeClass(value: unknown): string {
  if (value === '已接入' || value === '已处理') return 'badge-ok'
  if (value === '已隔离' || value === '已跳过') return 'badge-danger'
  if (value === '待裁决') return 'badge-warn'
  return 'badge-muted'
}

function toggleAll(event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  selectedIds.value = checked ? selectableRows.value.map((row) => Number(row.id)) : []
}

function resetCollectFilter() {
  collectFilter.value = { keyword: '', status: '', batch: '' }
  void reloadCollect()
}

function resetAdmissionFilter() {
  admissionFilter.value = { keyword: '', status: '', batch: '' }
  void reloadAdmissions()
}

function exportRows() {
  window.open(`${BASE}/export`, '_blank')
}

async function reloadCollect() {
  errorMessage.value = ''
  const query = new URLSearchParams(
    Object.entries(collectFilter.value).filter(([, v]) => v),
  ).toString()
  try {
    const response = await request(`${BASE}/collect-endpoints?${query}&size=200`)
    if (!response.ok) throw new Error('采集端点配置读取失败')
    const payload = await response.json()
    collectRows.value = (payload.items ?? []) as Row[]
    // 清掉已经不可选的选择项，避免提交已处理配置
    selectedIds.value = selectedIds.value.filter((id) =>
      collectRows.value.some((row) => Number(row.id) === id && isSelectable(row)),
    )
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集端点配置读取失败'
  }
}

async function reloadAdmissions() {
  errorMessage.value = ''
  const query = new URLSearchParams(
    Object.entries(admissionFilter.value).filter(([, v]) => v),
  ).toString()
  try {
    const response = await request(`${BASE}/records?${query}&size=200`)
    if (!response.ok) throw new Error('接入清单读取失败')
    const payload = await response.json()
    admissionRows.value = (payload.items ?? []) as Row[]
    admissionTotal.value = Number(payload.total ?? admissionRows.value.length)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '接入清单读取失败'
  }
}

async function runBatchAdmit() {
  errorMessage.value = ''
  resultMessage.value = ''
  try {
    const response = await request(`${BASE}/batch-admit`, {
      method: 'POST',
      body: JSON.stringify({ source_ids: selectedIds.value }),
    })
    if (!response.ok) throw new Error('批量准入未生效，请稍后重试')
    const payload = await response.json()
    lastOk.value = Boolean(payload.ok)
    resultMessage.value = payload.message || '批量准入已完成'
    selectedIds.value = []
    await Promise.all([reloadCollect(), reloadAdmissions()])
  } catch (error) {
    lastOk.value = false
    errorMessage.value = error instanceof Error ? error.message : '批量准入失败'
  }
}

async function resolve(row: Row, decision: 'admit' | 'reject') {
  errorMessage.value = ''
  const body = decision === 'admit'
    ? { decision, chosen_template: row['认证模板'] }
    : { decision }
  try {
    const response = await request(`${BASE}/records/${row.id}/resolve`, {
      method: 'POST',
      body: JSON.stringify(body),
    })
    if (!response.ok) throw new Error('冲突裁决未生效，请稍后重试')
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message || '冲突裁决被拒绝')
    resultMessage.value = payload.message
    lastOk.value = true
    await Promise.all([reloadCollect(), reloadAdmissions()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '冲突裁决失败'
  }
}

onMounted(async () => {
  await Promise.all([reloadCollect(), reloadAdmissions()])
})
</script>
