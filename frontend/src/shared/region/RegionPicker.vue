<!--
권역 고르기. 이름(강남-05)이나 ID(S05)로 검색해 목록에서 고르고, 목록에 없는 ID는 그대로 입력한다.
값(v-model)은 regionId 문자열이다. 입력칸 오른쪽에 고른 권역 이름을 보여 준다.
-->
<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'
import { regionOf, searchRegions } from './regions'

const props = withDefaults(
  defineProps<{ label: string; disabled?: boolean; readonly?: boolean; required?: boolean; placeholder?: string }>(),
  { disabled: false, readonly: false, required: false, placeholder: '이름이나 ID로 검색 · 예: 광진, S05' },
)
const value = defineModel<string>({ required: true })

const id = useId()
const open = ref(false)
const active = ref(0)
// 목록을 연 뒤 입력한 글자로만 거른다. 이미 고른 값으로 거르면 나머지 권역이 안 보인다.
const typed = ref(false)
const options = computed(() => searchRegions(typed.value ? value.value : ''))
const current = computed(() => regionOf(value.value.trim()))
const locked = computed(() => props.disabled || props.readonly)
const list = ref<HTMLElement | null>(null)

// 고른 항목이 목록 밖이면 보이게 스크롤한다
watch([active, open], () => list.value?.querySelector('.active')?.scrollIntoView({ block: 'nearest' }), { flush: 'post' })

function show() {
  if (locked.value) return
  open.value = true
  typed.value = false
  active.value = Math.max(0, options.value.findIndex((r) => r.id === value.value))
}

function pick(regionId: string) {
  value.value = regionId
  open.value = false
}

function onInput() {
  open.value = true
  typed.value = true
  active.value = 0
}

function move(step: number) {
  if (!open.value) return show()
  const n = options.value.length
  if (n) active.value = (active.value + step + n) % n
}

function enter(e: KeyboardEvent) {
  const r = options.value[active.value]
  // 목록이 열려 있으면 고르기만 하고 폼은 보내지 않는다
  if (open.value && r) {
    e.preventDefault()
    pick(r.id)
  }
}
</script>

<template>
  <div class="region-picker" @focusout="open = false">
    <label :for="id">{{ label }}</label>
    <div class="field">
      <input
        :id="id"
        v-model="value"
        role="combobox"
        autocomplete="off"
        aria-autocomplete="list"
        :aria-expanded="open"
        :aria-controls="`${id}-list`"
        :aria-activedescendant="open && options[active] ? `${id}-${options[active]!.id}` : undefined"
        :disabled="disabled"
        :readonly="readonly"
        :required="required"
        :placeholder="placeholder"
        @focus="show"
        @click="show"
        @input="onInput"
        @keydown.down.prevent="move(1)"
        @keydown.up.prevent="move(-1)"
        @keydown.enter="enter"
        @keydown.esc="open = false"
      />
      <span v-if="current" class="name" aria-hidden="true">{{ current.name }}</span>
      <ul v-if="open" :id="`${id}-list`" ref="list" role="listbox" :aria-label="`${label} 목록`">
        <li
          v-for="(r, i) in options"
          :id="`${id}-${r.id}`"
          :key="r.id"
          role="option"
          :aria-selected="r.id === value"
          :class="{ active: i === active }"
          @mousedown.prevent="pick(r.id)"
          @mouseenter="active = i"
        >
          <strong>{{ r.name }}</strong><span class="rid">{{ r.id }}</span><span v-if="r.note" class="note">{{ r.note }}</span>
        </li>
        <li v-if="!options.length" class="empty" role="presentation">목록에 없는 ID다. 그대로 쓰려면 이대로 둔다</li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.region-picker { display: flex; flex-direction: column; gap: 4px; min-width: 0; font-size: 13px; color: var(--ink-2); font-weight: 500; }
.field { position: relative; }
.field input { width: 100%; padding-right: 96px; }
.name { position: absolute; top: 50%; right: 10px; transform: translateY(-50%); max-width: 84px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; padding: 2px 8px; border-radius: 999px; background: var(--brand-soft); color: var(--brand-strong); font-size: 11.5px; font-weight: 700; pointer-events: none; }
ul { position: absolute; top: calc(100% + 4px); left: 0; right: 0; z-index: 30; max-height: 264px; overflow-y: auto; margin: 0; padding: 4px; list-style: none; background: #fff; border: 1px solid var(--line); border-radius: 10px; box-shadow: 0 12px 28px rgb(15 23 42 / 0.12); }
li { display: flex; align-items: baseline; gap: 8px; padding: 8px 10px; border-radius: 8px; cursor: pointer; color: var(--ink); }
li.active { background: var(--brand-soft); }
li[aria-selected='true'] strong { color: var(--brand-strong); }
.rid { color: var(--ink-3); font-size: 12px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.note { margin-left: auto; color: var(--ink-3); font-size: 11px; }
li.empty { cursor: default; color: var(--ink-3); font-size: 12px; }
</style>
