<script setup>
import { ref, computed } from 'vue'
import Button from 'primevue/button'

const props = defineProps({
  variables: {
    type: Object,
    default: () => ({ params: [], odes: [], algebraic: [] }),
  },
  activeKeys: { type: Array, default: () => [] },
})
const emit = defineEmits(['add-slider', 'toggle-output'])

const tabs = [
  { key: 'params', label: 'Params' },
  { key: 'odes', label: 'States' },
  { key: 'algebraic', label: 'Algebraic' },
]
const activeTab = ref('params')

const rows = computed(() => props.variables[activeTab.value] ?? [])
const activeSet = computed(() => new Set(props.activeKeys))
</script>

<template>
  <section class="variable-list">
    <h2>Variables</h2>
    <div class="tabs">
      <button
        v-for="t in tabs"
        :key="t.key"
        class="tab"
        :class="{ active: activeTab === t.key }"
        @click="activeTab = t.key"
      >
        {{ t.label }}
      </button>
    </div>
    <ul class="rows">
      <li v-for="qname in rows" :key="qname" class="var-row">
        <span class="qname" :title="qname">{{ qname }}</span>
        <Button
          v-if="activeTab === 'params'"
          icon="pi pi-plus"
          text
          rounded
          size="small"
          :disabled="activeSet.has(qname)"
          aria-label="add slider"
          @click="emit('add-slider', { qname })"
        />
      </li>
    </ul>
  </section>
</template>

<style scoped>
.variable-list {
  padding: 0.75rem;
  overflow-y: auto;
}
.tabs {
  display: flex;
  gap: 0.25rem;
  margin-bottom: 0.5rem;
}
.tab {
  background: transparent;
  border: 1px solid var(--p-content-border-color, #444);
  color: inherit;
  border-radius: 4px;
  padding: 0.2rem 0.5rem;
  cursor: pointer;
  font-size: 0.8rem;
}
.tab.active {
  background: var(--p-primary-color, #5b9bd5);
  color: #fff;
}
.rows {
  list-style: none;
  margin: 0;
  padding: 0;
}
.var-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.15rem 0;
  font-family: monospace;
  font-size: 0.8rem;
}
.qname {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
