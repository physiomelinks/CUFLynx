<script setup>
/**
 * A tab's settings: the standard ones, then the advanced ones in a collapsed "Advanced
 * settings" section (lib/advancedSettings). The tab renders each field itself, in the
 * default slot ({ item }), so a field looks and behaves the same in either section.
 *
 * The summary counts the advanced settings changed from their defaults (`changed`), so a
 * value set there is not forgotten once the section is closed.
 */
import { computed } from 'vue'
import { splitSettings } from '../lib/advancedSettings'

const props = defineProps({
  // the tab's key in advancedSettings.json: calibration, sensitivity, uq, emulator, ...
  tab: { type: String, required: true },
  // CA option descriptors and the tab's own fields ({name, own: true}), in form order
  items: { type: Array, default: () => [] },
  // (item) => whether its value differs from its default
  changed: { type: Function, default: () => false },
})

const parts = computed(() => splitSettings(props.tab, props.items))
const changedCount = computed(() => parts.value.advanced.filter((item) => props.changed(item)).length)
</script>

<template>
  <template v-for="item in parts.standard" :key="(item.own ? 'own:' : '') + item.name">
    <slot :item="item" />
  </template>
  <details v-if="parts.advanced.length" class="advanced-settings" :data-testid="`${tab}-advanced`">
    <summary>
      Advanced settings
      <span class="advanced-count">({{ parts.advanced.length }})</span>
      <span v-if="changedCount" class="advanced-changed" :data-testid="`${tab}-advanced-changed`">
        · {{ changedCount }} changed
      </span>
    </summary>
    <div class="advanced-body">
      <template v-for="item in parts.advanced" :key="(item.own ? 'own:' : '') + item.name">
        <slot :item="item" />
      </template>
    </div>
  </details>
</template>

<style scoped>
.advanced-settings {
  border-top: 1px solid var(--p-content-border-color, #ddd);
  padding-top: 0.3rem;
  font-size: 0.8rem;
}
.advanced-settings > summary {
  cursor: pointer;
  opacity: 0.8;
}
.advanced-count {
  opacity: 0.6;
}
.advanced-changed {
  color: #c07a00;
  font-weight: 600;
}
.advanced-body {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  padding-top: 0.3rem;
}
</style>
