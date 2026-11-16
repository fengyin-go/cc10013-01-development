<template>
  <section class="page" data-module="endpoint">
    <header class="page-head">
      <div>
        <h2>接口管理管理</h2>
        <p class="page-desc">维护API端点的输入、执行约束与结果记录，为接口管理提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记API端点</button>
        <button class="btn" type="button" @click="exportRows">导出接口管理清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <section class="todo-panel">
      <h3 class="block-title">接口管理待办区（接入清单回写）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>端点编号</th>
            <th>状态</th>
            <th>认证失败标记</th>
            <th>准入回执</th>
            <th>失败/隔离原因</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in todoRows" :key="`todo-${String(row.id)}`">
            <td>{{ row['端点编号'] ?? '—' }}</td>
            <td><em class="badge" :class="statusBadge(row.status)">{{ row.status ?? '—' }}</em></td>
            <td>
              <em v-if="row['认证失败标记']" class="badge badge-danger">认证失败</em>
              <span v-else class="muted-text">正常</span>
            </td>
            <td>{{ row['准入回执'] || '—' }}</td>
            <td :class="{ 'cell-warn': row['失败原因'] || row['隔离原因'] }">
              {{ row['失败原因'] || row['隔离原因'] || row['待裁决冲突'] || '—' }}
            </td>
          </tr>
          <tr v-if="!todoRows.length">
            <td colspan="5" class="empty-state">待办区暂无待处理端点，可在「端点接入清单」发起批量准入</td>
          </tr>
        </tbody>
      </table>
    </section>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无接口管理数据，可先登记API端点</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条接口管理记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/endpoint'
const columns = ["端点编号", "接口地址", "请求方法", "认证方式", "请求头配置", "参数模板", "响应格式", "超时秒数"]
const actions = ["提交校验", "激活端点", "停用端点"]
const statuses = ["草稿", "待校验", "已激活", "已停用"]
const stats = [{"label": "API端点总数", "value": 0}, {"label": "已激活端点", "value": 0}, {"label": "待校验端点", "value": 0}]

const rows = ref<Row[]>([])
const todoRows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

function statusBadge(value: unknown): string {
  if (value === '已激活') return 'badge-ok'
  if (value === '认证失败' || value === '已停用') return 'badge-danger'
  if (value === '待校验') return 'badge-warn'
  return 'badge-muted'
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = 'API端点登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('接口管理动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '接口管理操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const [listResp, todoResp] = await Promise.all([
      request(`${ENDPOINT}?${query}`),
      request(`${ENDPOINT}/todo`),
    ])
    if (!listResp.ok) {
      throw new Error('API端点列表读取失败')
    }
    const payload = await listResp.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    if (todoResp.ok) {
      const todoPayload = await todoResp.json()
      todoRows.value = todoPayload.items ?? []
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '接口管理列表读取失败'
  }
}

onMounted(reload)
</script>
