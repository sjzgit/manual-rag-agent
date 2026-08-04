<script setup lang="ts">
import MarkdownIt from 'markdown-it'
import { BookOpen, ExternalLink, X } from 'lucide-vue-next'
import { computed, ref } from 'vue'
import type { SourceChunk } from '../types'

const props = defineProps<{ sources: SourceChunk[] }>()

const md = new MarkdownIt({ html: false, linkify: true })

const items = computed(() => props.sources)

const active = ref<SourceChunk | null>(null)
const previewUrl = ref<string | null>(null)

function open(chunk: SourceChunk) {
  active.value = chunk
}

function close() {
  active.value = null
  previewUrl.value = null
}

function render(content: string) {
  return md.render(content)
}

function onBodyClick(e: MouseEvent) {
  const target = e.target as HTMLElement
  if (target.tagName === 'IMG') {
    const src = (target as HTMLImageElement).getAttribute('src')
    if (src) previewUrl.value = src
  }
}
</script>

<template>
  <div v-if="items.length" class="mt-3 border-t border-muted pt-2.5">
    <div class="mb-2 flex items-center gap-1.5 text-[11px] font-medium text-ink-sub">
      <BookOpen :size="13" class="text-primary" />
      <span>来源原文（{{ items.length }}）</span>
      <span class="ml-auto text-[10px] text-ink-sub/50">点击查看图文原文</span>
    </div>

    <div class="flex flex-col gap-1.5">
      <button
        v-for="s in items"
        :key="s.chunk_id"
        class="group flex cursor-pointer items-center gap-2 rounded-xl border border-muted bg-muted/40 px-3 py-2 text-left transition-all hover:border-primary/30 hover:bg-white"
        @click="open(s)"
      >
        <span class="min-w-0 flex-1 truncate text-[12px] font-medium text-ink group-hover:text-primary">
          {{ s.doc }} &gt; {{ s.path }}
        </span>
        <span class="shrink-0 rounded bg-primary/10 px-1.5 py-0.5 text-[11px] text-primary">
          {{ (s.score * 100).toFixed(0) }}%
        </span>
        <ExternalLink :size="13" class="shrink-0 text-ink-sub/50 group-hover:text-primary" />
      </button>
    </div>
  </div>

  <!-- 来源原文弹窗：点击后弹出，图文混排展示切片原文 -->
  <Teleport to="body">
    <div
      v-if="active"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm"
      @click.self="close"
    >
      <div class="flex max-h-[85vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
        <div class="flex items-center gap-2 border-b border-muted px-4 py-3">
          <BookOpen :size="16" class="shrink-0 text-primary" />
          <div class="min-w-0 flex-1">
            <div class="truncate text-[13px] font-semibold text-ink">{{ active.doc }}</div>
            <div class="truncate text-[11px] text-ink-sub">{{ active.path }}</div>
          </div>
          <span class="shrink-0 rounded bg-primary/10 px-2 py-0.5 text-[11px] text-primary">
            相关度 {{ (active.score * 100).toFixed(0) }}%
          </span>
          <button
            class="cursor-pointer rounded-md p-1.5 text-ink-sub transition-colors hover:bg-muted hover:text-ink"
            @click="close"
          >
            <X :size="16" />
          </button>
        </div>
        <div class="overflow-y-auto px-4 py-3.5" @click="onBodyClick">
          <div class="md-body text-[13px] leading-6" v-html="render(active.content)" />
        </div>
      </div>
    </div>
  </Teleport>

  <!-- 图片放大预览 -->
  <Teleport to="body">
    <div
      v-if="previewUrl"
      class="fixed inset-0 z-[60] flex cursor-zoom-out items-center justify-center bg-black/70 backdrop-blur-sm"
      @click="previewUrl = null"
    >
      <img :src="previewUrl" class="max-h-[85vh] max-w-[90vw] rounded-xl shadow-2xl" />
    </div>
  </Teleport>
</template>
