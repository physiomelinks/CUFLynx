<script setup>
// A number input that DISPLAYS large/small values in scientific notation while
// keeping full precision in the model. When focused it shows the raw editable
// number (no reformatting mid-type); when blurred it shows the formatted value.
// Emits update:modelValue with the parsed number (or null when blank).
import { computed, ref } from 'vue'
import { fmtSci } from '../lib/format'

const props = defineProps({
  modelValue: { type: [Number, String, null], default: null },
})
const emit = defineEmits(['update:modelValue'])

const focused = ref(false)
const buffer = ref('') // what the user is typing while focused

// Blurred: the formatted value. Focused: the raw text the user is editing.
const display = computed(() => (focused.value ? buffer.value : fmtSci(props.modelValue)))

function onFocus(e) {
  focused.value = true
  buffer.value = props.modelValue == null || props.modelValue === '' ? '' : String(props.modelValue)
  e.target.select?.()
}
function onInput(e) {
  buffer.value = e.target.value
  const s = e.target.value.trim()
  // Number('1.5e-8') works; blank -> null; anything unparseable -> null so the
  // model never holds NaN.
  const n = Number(s)
  emit('update:modelValue', s === '' || !Number.isFinite(n) ? null : n)
}
function onBlur() {
  focused.value = false
}
</script>

<template>
  <input
    type="text"
    inputmode="decimal"
    :value="display"
    v-bind="$attrs"
    @focus="onFocus"
    @input="onInput"
    @blur="onBlur"
  />
</template>
