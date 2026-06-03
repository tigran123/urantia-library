<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { CheckIcon, XMarkIcon, TrashIcon, PencilIcon, GlobeAltIcon, LockClosedIcon } from '@heroicons/vue/24/outline'
import type { Annotation } from '../api'
import type { PopoverPosition } from '../lib/anchors/popoverPosition'

/* The popover plays two roles:
   - "create" mode: a fresh selection was made; user picks Highlight / +Note /
     Public, then Save. Host provides selection data via `pending`.
   - "view" mode: an existing highlight was clicked; show its note (if any),
     author, status, and edit/delete buttons (only when is_own). Host provides
     the annotation via `existing`.
   In both modes the host also provides `position` — the popover sits
   directly below the selection at a fixed width, horizontally clamped to
   stay within the scroll container. Below-placement is critical on narrow
   mobile viewports where side placement would fall off-screen. */

interface PendingSelection {
  selectedText: string
}

const props = defineProps<{
  pending: PendingSelection | null
  existing: Annotation | null
  position: PopoverPosition | null
  canEdit: boolean
}>()

const emit = defineEmits<{
  (e: 'save', payload: { body: string | null; isPublic: boolean }): void
  (e: 'update', payload: { id: number; body: string | null; isPublic: boolean }): void
  (e: 'delete', id: number): void
  (e: 'note-mode'): void
  (e: 'close'): void
}>()

const { t } = useI18n({ useScope: 'global' })

const showNoteInput = ref(false)
const isPublic = ref(false)
const body = ref('')
const editingExisting = ref(false)

const noteRef = ref<HTMLTextAreaElement | null>(null)

watch(() => props.pending, (v) => {
  if (v) {
    showNoteInput.value = false
    isPublic.value = false
    body.value = ''
    editingExisting.value = false
  }
})

watch(() => props.existing, (v) => {
  if (v) {
    body.value = v.body || ''
    isPublic.value = v.is_public
    editingExisting.value = false
    showNoteInput.value = false
  }
})

const isOpen = computed(() => !!props.pending || !!props.existing)

const popoverStyle = computed(() => {
  const p = props.position
  if (!p) return undefined
  return {
    left: `${p.left}px`,
    top: `${p.top}px`,
    width: `${p.width}px`,
  }
})

const onClose = () => emit('close')

const beginAddNote = async () => {
  showNoteInput.value = true
  await nextTick()
  noteRef.value?.focus()
  // Focusing the textarea cleared the native page selection; ask the host to
  // paint a persistent highlight so the selected text stays visible.
  emit('note-mode')
}

const onSaveCreate = () => {
  const text = body.value.trim()
  emit('save', { body: text || null, isPublic: isPublic.value })
}

const onSaveUpdate = () => {
  if (!props.existing) return
  const text = body.value.trim()
  emit('update', { id: props.existing.id, body: text || null, isPublic: isPublic.value })
}

const onDelete = () => {
  if (!props.existing) return
  if (!confirm(t('app.annotation_delete_confirm'))) return
  emit('delete', props.existing.id)
}

const beginEdit = async () => {
  editingExisting.value = true
  await nextTick()
  noteRef.value?.focus()
}
</script>

<template>
  <div
    v-if="isOpen"
    class="anno-popover"
    :style="popoverStyle"
    role="dialog"
    @mousedown.stop
    @click.stop
  >
    <!-- Create mode -->
    <template v-if="pending">
      <div v-if="!showNoteInput" class="flex flex-wrap items-center gap-1">
        <button
          @click="onSaveCreate"
          :title="t('app.annotation_highlight')"
          class="anno-btn"
        >
          <span class="inline-block w-4 h-4 rounded-sm anno-swatch-mine"></span>
          <span class="text-sm">{{ t('app.annotation_highlight') }}</span>
        </button>
        <button
          @click="beginAddNote"
          :title="t('app.annotation_add_note')"
          class="anno-btn"
        >
          <PencilIcon class="w-4 h-4" />
          <span class="text-sm">{{ t('app.annotation_add_note') }}</span>
        </button>
        <button
          @click="isPublic = !isPublic"
          :title="isPublic ? t('app.annotation_public') : t('app.annotation_private')"
          :class="['anno-btn', isPublic ? 'anno-btn-on' : '']"
        >
          <GlobeAltIcon v-if="isPublic" class="w-4 h-4" />
          <LockClosedIcon v-else class="w-4 h-4" />
          <span class="text-sm">{{ isPublic ? t('app.annotation_public') : t('app.annotation_private') }}</span>
        </button>
        <button @click="onClose" :title="t('app.cancel')" class="anno-btn anno-btn-icon ml-auto">
          <XMarkIcon class="w-4 h-4" />
        </button>
      </div>
      <div v-else class="flex flex-col gap-2">
        <textarea
          ref="noteRef"
          v-model="body"
          :placeholder="t('app.annotation_note_placeholder')"
          rows="3"
          class="anno-textarea"
        ></textarea>
        <div class="flex items-center justify-between gap-2 flex-wrap">
          <label
            :class="['flex items-center gap-1 text-sm cursor-pointer px-2 py-1 rounded', isPublic ? 'anno-btn-on' : '']"
          >
            <input type="checkbox" v-model="isPublic" class="anno-checkbox" />
            <GlobeAltIcon v-if="isPublic" class="w-4 h-4" />
            <LockClosedIcon v-else class="w-4 h-4" />
            <span>{{ isPublic ? t('app.annotation_public') : t('app.annotation_private') }}</span>
          </label>
          <div class="flex items-center gap-1">
            <button @click="onClose" class="anno-btn">
              <XMarkIcon class="w-4 h-4" />
              <span class="text-sm">{{ t('app.cancel') }}</span>
            </button>
            <button @click="onSaveCreate" class="anno-btn anno-btn-primary">
              <CheckIcon class="w-4 h-4" />
              <span class="text-sm">{{ t('app.annotation_save') }}</span>
            </button>
          </div>
        </div>
      </div>
    </template>

    <!-- View / edit existing -->
    <template v-else-if="existing">
      <div class="flex flex-col gap-2">
        <div class="text-xs text-gray-500 dark:text-gray-400 flex items-center justify-between gap-2">
          <span class="truncate">
            {{ t('app.annotation_by', { name: existing.author_name }) }}
          </span>
          <span
            v-if="existing.is_public"
            class="inline-flex items-center gap-1 px-2 py-0.5 rounded anno-btn-on"
          >
            <GlobeAltIcon class="w-3.5 h-3.5" />
            {{ existing.status === 'pending' ? t('app.pending_moderation') : t('app.annotation_public') }}
          </span>
          <span v-else class="inline-flex items-center gap-1">
            <LockClosedIcon class="w-3.5 h-3.5" />
            {{ t('app.annotation_private') }}
          </span>
        </div>

        <blockquote class="anno-snippet">{{ existing.selected_text }}</blockquote>

        <template v-if="!editingExisting">
          <p v-if="existing.body" class="text-sm whitespace-pre-wrap text-gray-900 dark:text-gray-100">{{ existing.body }}</p>
          <p v-if="existing.is_public && existing.status === 'pending' && existing.is_own"
             class="text-xs italic text-amber-600 dark:text-amber-400">
            {{ t('app.annotation_pending_note') }}
          </p>

          <div class="flex items-center justify-end gap-1">
            <button v-if="canEdit" @click="beginEdit" class="anno-btn">
              <PencilIcon class="w-4 h-4" />
              <span class="text-sm">{{ t('app.edit') }}</span>
            </button>
            <button v-if="canEdit" @click="onDelete" class="anno-btn anno-btn-danger">
              <TrashIcon class="w-4 h-4" />
              <span class="text-sm">{{ t('app.delete') }}</span>
            </button>
            <button @click="onClose" class="anno-btn anno-btn-icon">
              <XMarkIcon class="w-4 h-4" />
            </button>
          </div>
        </template>

        <template v-else>
          <textarea
            ref="noteRef"
            v-model="body"
            :placeholder="t('app.annotation_note_placeholder')"
            rows="3"
            class="anno-textarea"
          ></textarea>
          <div class="flex items-center justify-between gap-2">
            <label
              :class="['flex items-center gap-1 text-sm cursor-pointer px-2 py-1 rounded', isPublic ? 'anno-btn-on' : '']"
            >
              <input type="checkbox" v-model="isPublic" class="anno-checkbox" />
              <GlobeAltIcon v-if="isPublic" class="w-4 h-4" />
              <LockClosedIcon v-else class="w-4 h-4" />
              <span>{{ isPublic ? t('app.annotation_public') : t('app.annotation_private') }}</span>
            </label>
            <div class="flex items-center gap-1">
              <button @click="editingExisting = false" class="anno-btn">
                <XMarkIcon class="w-4 h-4" />
                <span class="text-sm">{{ t('app.cancel') }}</span>
              </button>
              <button @click="onSaveUpdate" class="anno-btn anno-btn-primary">
                <CheckIcon class="w-4 h-4" />
                <span class="text-sm">{{ t('app.annotation_save') }}</span>
              </button>
            </div>
          </div>
        </template>
      </div>
    </template>
  </div>
</template>

<style>
.anno-popover {
  position: absolute;
  z-index: 60;
  background: white;
  color: #111827;
  border: 1px solid rgba(0, 0, 0, 0.12);
  border-radius: 8px;
  box-shadow: 0 10px 25px rgba(0, 0, 0, 0.18);
  padding: 8px;
  user-select: none;
  max-width: calc(100vw - 16px);
}
.dark .anno-popover {
  background: #111827;
  color: #f3f4f6;
  border-color: rgba(255, 255, 255, 0.16);
}
.anno-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border-radius: 6px;
  background: rgba(0, 0, 0, 0.05);
  color: inherit;
  cursor: pointer;
}
.anno-btn:hover { background: rgba(0, 0, 0, 0.1); }
.dark .anno-btn { background: rgba(255, 255, 255, 0.08); }
.dark .anno-btn:hover { background: rgba(255, 255, 255, 0.15); }
.anno-btn-icon { padding: 4px; }
.anno-btn-primary {
  background: #2563eb;
  color: white;
}
.anno-btn-primary:hover { background: #1d4ed8; }
.anno-btn-danger {
  color: #b91c1c;
}
.dark .anno-btn-danger { color: #fca5a5; }
.anno-btn-on {
  background: rgba(37, 99, 235, 0.15);
  color: #1d4ed8;
}
.dark .anno-btn-on {
  background: rgba(96, 165, 250, 0.2);
  color: #93c5fd;
}
.anno-textarea {
  width: 100%;
  padding: 6px 8px;
  border: 1px solid rgba(0, 0, 0, 0.18);
  border-radius: 6px;
  background: white;
  color: #111827;
  font-size: 0.9rem;
  resize: vertical;
}
.dark .anno-textarea {
  background: #1f2937;
  color: #f3f4f6;
  border-color: rgba(255, 255, 255, 0.16);
}
.anno-textarea:focus { outline: none; border-color: #2563eb; }
.anno-checkbox { cursor: pointer; }
.anno-snippet {
  border-left: 3px solid rgba(127, 127, 127, 0.4);
  padding: 4px 8px;
  font-style: italic;
  color: #4b5563;
  font-size: 0.875rem;
  max-height: 4.5rem;
  overflow: auto;
}
.dark .anno-snippet { color: #d1d5db; }
.anno-swatch-mine { background: rgba(255, 230, 0, 0.7); border: 1px solid rgba(0,0,0,0.2); }
</style>
