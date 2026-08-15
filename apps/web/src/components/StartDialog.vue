<script setup>
import Dialog from 'primevue/dialog'
import Button from 'primevue/button'
import {
  PHLYNX_URL,
  PMR_URL,
  EXAMPLE_MODELS,
  EXTERNAL_PYTHON_TUTORIAL_URL,
} from '../lib/examples'

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

      <!--
        The fourth way in is not a model description at all: the user brings the
        solver. CUFLynx calibrates a Python class the same way it calibrates a
        CellML model, so it belongs beside the other three starting points.
      -->
      <section class="start-section">
        <h3>External Python</h3>
        <p class="start-hint">
          Bring your own solver — a Python class CUFLynx calibrates like any other
          model.
        </p>
        <ol class="start-steps" data-testid="start-external-python-steps">
          <li>
            Write the class with <code>SIM_HELPER = MyClass</code> at the bottom of
            the file.
          </li>
          <li>Drop the <code>.py</code> here, on the model box.</li>
          <li>
            Pick the interpreter that has its dependencies in
            <strong>Settings</strong>.
          </li>
        </ol>
        <a
          :href="EXTERNAL_PYTHON_TUTORIAL_URL"
          target="_blank"
          rel="noopener"
          class="phlynx-link"
          data-testid="start-external-python-link"
        >
          <i class="pi pi-external-link" /> Open the External Python tutorial
        </a>
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
/* The three steps are a summary, not the tutorial: same size as the blurb so
   they read as one paragraph with numbers, and the link below carries the rest. */
.start-steps {
  margin: 0 0 0.5rem;
  padding-left: 1.1rem;
  opacity: 0.7;
  font-size: 0.8rem;
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
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
