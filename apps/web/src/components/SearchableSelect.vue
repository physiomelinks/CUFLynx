<script setup>
/**
 * A <select> you can type into (issue #160).
 *
 * The lists this replaces are long enough to be unusable as plain dropdowns: a
 * model's operand list is every variable it has, and the operation list grows
 * with every user function. Scrolling to find `aortic_root/v` among 456 entries
 * is the problem; typing three characters is the fix.
 *
 * Modelled on the "add controlled parameter" picker in ProtocolInfoEditor,
 * which learned the same lesson: the matches are listed *as you type*, so
 * searching and seeing the results are one act rather than type-then-open.
 *
 * Behaves like a select from the outside — v-model, options, an empty choice —
 * so callers do not have to think about focus or filtering.
 */
import { computed, nextTick, ref, watch } from 'vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  options: { type: Array, default: () => [] },
  // Shown for the empty choice, and as the placeholder when nothing is chosen.
  placeholder: { type: String, default: '—' },
  // Rendered instead of the raw value (the operation list shows '(none)').
  labelFor: { type: Function, default: null },
  // Extra class per option, e.g. to grey out a non-differentiable operation.
  classFor: { type: Function, default: null },
  disabled: { type: Boolean, default: false },
  testid: { type: String, default: 'searchable-select' },
})
const emit = defineEmits(['update:modelValue', 'focus'])

const open = ref(false)
const query = ref('')
const highlight = ref(-1)
const input = ref(null)
const trigger = ref(null)
// Where to draw the list. Fixed, not absolute: this widget lives inside a
// scrolling list of data_items, and an absolutely positioned dropdown is
// *clipped* by that container's overflow — as well as painting under the
// fields that follow it. Fixed coordinates escape both, at the cost of having
// to measure the trigger when the list opens.
const anchor = ref({ left: 0, top: 0, width: 0 })

const label = (value) => (props.labelFor ? props.labelFor(value) : value || props.placeholder)

// The empty choice is always offered: clearing a field is a thing people do,
// and a search box with no way back to "unset" traps them.
const allOptions = computed(() => ['', ...props.options.filter((o) => o !== '')])

const matches = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return allOptions.value
  return allOptions.value.filter((o) => label(o).toLowerCase().includes(q))
})

// A query that no longer matches anything the list still holds would leave the
// highlight pointing past the end.
watch(matches, (list) => {
  if (highlight.value >= list.length) highlight.value = list.length - 1
})

function measure(el) {
  const rect = el?.getBoundingClientRect?.()
  if (!rect) return
  anchor.value = { left: rect.left, top: rect.bottom, width: rect.width }
}

function show(event) {
  if (props.disabled) return
  measure(event?.currentTarget ?? trigger.value)
  open.value = true
  query.value = ''
  highlight.value = -1
  emit('focus')
  nextTick(() => input.value?.focus())
}

function choose(value) {
  emit('update:modelValue', value)
  open.value = false
  query.value = ''
}

function onKey(event) {
  const list = matches.value
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    highlight.value = Math.min(highlight.value + 1, list.length - 1)
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    highlight.value = Math.max(highlight.value - 1, 0)
  } else if (event.key === 'Enter') {
    event.preventDefault()
    // With nothing highlighted, a single match is unambiguous — pressing enter
    // after typing enough to narrow it to one should just take it.
    const pick = highlight.value >= 0 ? list[highlight.value] : list.length === 1 ? list[0] : null
    if (pick !== null && pick !== undefined) choose(pick)
  } else if (event.key === 'Escape') {
    open.value = false
    query.value = ''
  }
}
</script>

<template>
  <span class="ss" :class="{ 'ss-disabled': disabled }">
    <!-- Closed: reads as the current value, like the select it replaces. -->
    <button
      v-if="!open"
      ref="trigger"
      type="button"
      class="ss-value"
      :class="{ 'ss-unset': !modelValue }"
      :disabled="disabled"
      :title="modelValue || placeholder"
      :data-testid="testid"
      @click="show"
      @focus="show"
    >
      {{ label(modelValue) }}
    </button>

    <template v-else>
      <input
        ref="input"
        v-model="query"
        type="text"
        class="ss-search"
        :placeholder="`type to search — ${matches.length} of ${allOptions.length}`"
        :data-testid="`${testid}-search`"
        @keydown="onKey"
        @blur="open = false"
      />
      <!--
        mousedown, not click: blur fires first on click and would close the list
        before the selection landed.
      -->
      <ul
        v-if="matches.length"
        class="ss-options"
        :style="{ left: `${anchor.left}px`, top: `${anchor.top}px`, minWidth: `${anchor.width}px` }"
        :data-testid="`${testid}-options`"
      >
        <li
          v-for="(option, i) in matches"
          :key="option || '(empty)'"
          class="ss-option"
          :class="[{ active: i === highlight }, classFor ? classFor(option) : '']"
          :data-testid="`${testid}-option`"
          @mousedown.prevent="choose(option)"
          @mouseenter="highlight = i"
        >
          {{ label(option) }}
        </li>
      </ul>
      <p
        v-else
        class="ss-empty"
        :style="{ left: `${anchor.left}px`, top: `${anchor.top}px` }"
        :data-testid="`${testid}-empty`"
      >
        Nothing matches “{{ query }}”.
      </p>
    </template>
  </span>
</template>

<style scoped>
.ss {
  position: relative;
  display: inline-block;
  min-width: 0;
}
.ss-value,
.ss-search {
  font: inherit;
  /* No intrinsic size: the host decides, so this sits in a form grid at the
     same height and width as the inputs beside it. */
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
  padding: 0.15rem 0.35rem;
  border: 1px solid var(--p-content-border-color, #ccc);
  border-radius: 3px;
  background: var(--p-content-background, #fff);
  color: inherit;
  text-align: left;
  text-overflow: ellipsis;
  overflow: hidden;
  white-space: nowrap;
}
.ss-value {
  cursor: pointer;
}
.ss-unset {
  opacity: 0.7;
}
.ss-disabled .ss-value {
  cursor: not-allowed;
}
.ss-options {
  position: fixed;
  z-index: 3000;
  max-width: 26rem;
  max-height: 14rem;
  overflow-y: auto;
  margin: 0.15rem 0 0;
  padding: 0;
  list-style: none;
  border: 1px solid var(--p-content-border-color, #ccc);
  border-radius: 3px;
  background: var(--p-content-background, #fff);
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.14);
}
.ss-option {
  padding: 0.18rem 0.45rem;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ss-option.active,
.ss-option:hover {
  background: var(--p-highlight-background, #e8f0fe);
}
.ss-empty {
  position: fixed;
  z-index: 3000;
  margin: 0.15rem 0 0;
  padding: 0.2rem 0.45rem;
  font-size: 0.8rem;
  opacity: 0.75;
  background: var(--p-content-background, #fff);
  border: 1px solid var(--p-content-border-color, #ccc);
  border-radius: 3px;
  white-space: nowrap;
}
</style>
