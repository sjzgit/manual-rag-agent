<script setup lang="ts">
/** 来源引用卡片：chips 列表 → 点击弹窗预览原文 Markdown + 图片 */
import MarkdownIt from 'markdown-it'
import { BookOpen, X } from 'lucide-vue-next'
import { computed, ref } from 'vue'
import type { SourceChunk } from '../types'

const props = defineProps<{ sources: SourceChunk[] }>()

const md = new MarkdownIt({ html: false, linkify: true })

const activeSource = ref<SourceChunk | null>(null)
const previewUrl = ref<string | null>(null)

const items = computed(() => props.sources)

function open(chunk: SourceChunk) {
  activeSource.value = chunk
}

function close() {
  activeSource.value = null
  previewUrl.value = null
}

function render(content: string) {
  return md.render(content)
}
</script>

<template>
  <div v-if="items.length" class="mt-3 border-t border-muted pt-2.5">
    <div class="mb-1.5 flex items-center gap-1.5 text-[12px] text-ink-sub">
      <BookOpen :size="13" class="text-primary" />
      <span>来源（{{ items.length }}）</span>
    </div>

    <div class="flex flex-wrap gap-1.5">
      <button
        v-for="s in items"
        :key="s.chunk_id"
        class="group flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12px] transition-all duration-200 cursor-pointer border-muted bg-muted/60 text-ink-sub hover:border-primary/30 hover:text-primary hover:shadow-lift"
        @click="open(s)"
      >
        <span class="max-w-[280px] truncate">{{ s.doc }} &gt; {{ s.path }}</span>
        <span class="text-[11px] opacity-70">{{ s.score.toFixed(2) }}</span>
        <span class="ml-0.5 rounded bg-primary/10 px-1 py-0.5 text-[10px] text-primary opacity-0 transition-opacity group-hover:opacity-100">预览</span>
      </button>
    </div>
  </div>

  <!-- 弹窗：引用原文预览 -->
  <Teleport to="body">
    <div
      v-if="activeSource"
      class="fixed inset-0 z-50 flex items-center justify-center p-6"
    >
      <!-- 遮罩 -->
      <div class="absolute inset-0 bg-black/40 backdrop-blur-sm" @click="close" />

      <!-- 弹窗内容 -->
      <div class="relative z-10 max-h-[80vh] w-full max-w-2xl overflow-hidden rounded-2xl bg-white shadow-2xl">
        <!-- 头部 -->
        <div class="flex items-center gap-3 border-b border-muted px-5 py-3.5">
          <div class="flex-1 min-w-0">
            <div class="text-[14px] font-semibold text-ink truncate">
              {{ activeSource.doc }}
            </div>
            <div class="text-[12px] text-ink-sub truncate">
              {{ activeSource.path }} · 相关度 {{ (activeSource.score * 100).toFixed(0) }}%
            </div>
          </div>
          <button
            class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-ink-sub transition-colors hover:bg-muted hover:text-ink cursor-pointer"
            @click="close"
          >
            <X :size="16" />
          </button>
        </div>

        <!-- 正文 -->
        <div class="overflow-y-auto px-5 py-4" style="max-height: calc(80vh - 60px)">
          <div class="md-body text-[14px] leading-7" v-html="render(activeSource.content)" />

          <!-- 图片网格 -->
          <div v-if="activeSource.images.length" class="mt-4 border-t border-muted pt-4">
            <div class="mb-2 text-[12px] font-medium text-ink-sub">
              关联图片（{{ activeSource.images.length }}）
            </div>
            <div
              class="grid gap-2"
              :style="{ gridTemplateColumns: `repeat(${Math.min(activeSource.images.length, 3)}, minmax(0, 1fr))` }"
            >
              <button
                v-for="(img, i) in activeSource.images"
                :key="i"
                class="group overflow-hidden rounded-lg border border-muted bg-white cursor-zoom-in transition-shadow hover:shadow-md"
                @click="previewUrl = img"
              >
                <img
                  :src="img"
                  :alt="`截图 ${i + 1}`"
                  class="h-24 w-full object-cover transition-transform duration-300 group-hover:scale-105"
                  loading="lazy"
                />
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- 图片放大预览 -->
      <div
        v-if="previewUrl"
        class="absolute inset-0 z-20 flex items-center justify-center bg-black/70 backdrop-blur-sm cursor-zoom-out"
        @click="previewUrl = null"
      >
        <img :src="previewUrl" class="max-h-[88vh] max-w-[92vw] rounded-xl shadow-2xl" />
      </div>
    </div>
  </Teleport>
</template>
