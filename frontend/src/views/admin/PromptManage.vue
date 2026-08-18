<script setup lang="ts">
/** 提示词模板管理：查看与编辑 RAG 各环节提示词 */
import { ElMessage } from 'element-plus'
import { onMounted, ref } from 'vue'
import { listPrompts, updatePrompt } from '../../api/admin'
import type { PromptItem } from '../../types'

const prompts = ref<PromptItem[]>([])
const loading = ref(false)
const dialogVisible = ref(false)
const editing = ref<PromptItem | null>(null)
const editContent = ref('')
const saving = ref(false)

async function refresh() {
  loading.value = true
  try {
    const data = await listPrompts()
    prompts.value = data.prompts
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

function openEdit(row: PromptItem) {
  editing.value = row
  editContent.value = row.content
  dialogVisible.value = true
}

async function save() {
  if (!editing.value) return
  saving.value = true
  try {
    await updatePrompt(editing.value.key, editContent.value)
    ElMessage.success('已保存并生效')
    dialogVisible.value = false
    editing.value = null
    await refresh()
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    saving.value = false
  }
}

onMounted(refresh)
</script>

<template>
  <div class="space-y-4">
    <div>
      <h2 class="text-[16px] font-semibold text-ink">提示词模板管理</h2>
      <p class="text-[12px] text-ink-sub">编辑 RAG 各环节提示词，保存后即时生效（与架构绑定，不支持新增）</p>
    </div>

    <el-table :data="prompts" v-loading="loading" border class="rounded-xl">
      <el-table-column prop="name" label="模板" width="200" />
      <el-table-column prop="key" label="标识" width="220" />
      <el-table-column label="状态" width="120">
        <template #default="{ row }">
          <el-tag :type="row.is_customized ? 'primary' : 'info'" size="small">
            {{ row.is_customized ? '已自定义' : '默认' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="内容预览" min-width="260">
        <template #default="{ row }">
          <span class="truncate text-[12px] text-ink-sub">{{ row.content }}</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="120" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog
      v-model="dialogVisible"
      :title="editing ? `编辑「${editing.name}」` : ''"
      width="760px"
      top="6vh"
      destroy-on-close
    >
      <el-input
        v-model="editContent"
        type="textarea"
        :rows="20"
        placeholder="请输入提示词内容"
      />
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>
