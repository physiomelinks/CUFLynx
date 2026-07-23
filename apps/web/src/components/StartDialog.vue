<script setup>
import Dialog from 'primevue/dialog'
import Button from 'primevue/button'
import { PHLYNX_URL, PMR_URL, EXAMPLE_MODELS } from '../lib/examples'

defineProps({
  visible: { type: Boolean, default: false },
})
const emit = defineEmits(['update:visible', 'select-example'])

function chooseExample(example) {
  emit('select-example', example)
  emit('update:visible', false)
}
</script>

<template>
  <Dialog
    :visible="visible"
    modal
    header="Create a model"
    :style="{ width: '30rem' }"
    data-testid="start-dialog"
    @update:visible="emit('update:visible', $event)"
  >
    <div class="start-body">
      <section class="start-section">
        <h3>Build your own</h3>
        <p class="start-hint">
          Design a new CellML model from scratch in PhLynx, the model builder.
        </p>
        <a
          :href="PHLYNX_URL"
          target="_blank"
          rel="noopener"
          class="phlynx-link"
          data-testid="start-phlynx-link"
        >
          <i class="pi pi-external-link" /> Open PhLynx
        </a>
      </section>

      <section class="start-section">
        <h3>Download from the Physiome Model Repository</h3>
        <p class="start-hint">
          Browse the PMR and download a published CellML model, then drop the file here.
        </p>
        <a
          :href="PMR_URL"
          target="_blank"
          rel="noopener"
          class="phlynx-link"
          data-testid="start-pmr-link"
        >
          <i class="pi pi-external-link" /> Open the Physiome Model Repository
        </a>
      </section>

      <section class="start-section">
        <h3>Start from an example</h3>
        <p class="start-hint">Load a bundled example model to explore.</p>
        <ul class="example-list">
          <li v-for="ex in EXAMPLE_MODELS" :key="ex.name">
            <Button
              :label="ex.label"
              icon="pi pi-file"
              size="small"
              text
              :data-testid="`start-example-${ex.name}`"
              @click="chooseExample(ex)"
            />
          </li>
        </ul>
      </section>
    </div>
  </Dialog>
</template>

<style scoped>
.start-body {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}
.start-section h3 {
  margin: 0 0 0.25rem;
  font-size: 0.95rem;
}
.start-hint {
  margin: 0 0 0.5rem;
  opacity: 0.7;
  font-size: 0.8rem;
}
.phlynx-link {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  color: var(--p-primary-color, #5b9bd5);
  text-decoration: none;
}
.phlynx-link:hover {
  text-decoration: underline;
}
.example-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
</style>
