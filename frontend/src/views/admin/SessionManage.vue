<script setup lang="ts">
/** 会话管理：会话列表 + 详情（复用 SessionDetailDrawer 组件） */
import { ElMessage } from 'element-plus'
import { onMounted, ref } from 'vue'
import { listAdminSessions } from '../../api/admin'
import type { AdminSessionItem } from '../../types'
import SessionDetailDrawer from './SessionDetailDrawer.vue'

const sessions = ref<AdminSessionItem[]>([])
const total = ref(0)
const loading = ref(false)

const drawerVisible = ref(false)
const selectedSessionId = ref('')

async function refresh() {
  loading.value = true
  try {
    const data = await listAdminSessions(0, 100)
    sessions.value = data.sessions
    total.value = data.total
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

function openDetail(row: AdminSessionItem) {
  selectedSessionId.value = row.id
  drawerVisible.value = true
}

onMounted(refresh)
</script>

<template>
  <div class="space-y-4">
    <div>
      <h2 class="text-[16px] font-semibold text-ink">会话管理</h2>
      <p class="text-[12px] text-ink-sub">共 {{ total }} 个会话，点击查看进入会话详情</p>
    </div>

    <el-table :data="sessions" v-loading="loading" border class="rounded-xl">
      <el-table-column prop="title" label="会话标题" min-width="260" />
      <el-table-column prop="doc_filter" label="文档过滤" width="180">
        <template #default="{ row }">{{ row.doc_filter ?? '-' }}</template>
      </el-table-column>
      <el-table-column prop="updated_at" label="更新时间" width="180" />
      <el-table-column label="操作" width="120" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openDetail(row)">查看</el-button>
        </template>
      </el-table-column>
    </el-table>

    <SessionDetailDrawer v-model="drawerVisible" :session-id="selectedSessionId" />
  </div>
</template>
