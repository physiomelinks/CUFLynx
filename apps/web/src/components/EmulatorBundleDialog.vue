<script setup>
import { computed, ref, watch } from 'vue'
import Dialog from 'primevue/dialog'
import Button from 'primevue/button'

/**
 * Choose which trained emulator this study is read with.
 *
 * A study normally has one, and this never opens. It exists for the study that
 * fits *one* design with several emulator families -- the same simulations under
 * RBF, an MLP and a forest -- where every bundle is equally "the emulator for
 * this study" and the backend would otherwise fall back to whichever was written
 * last. Recency is not a reason to trust an emulator, so the choice is put here.
 *
 * The two numbers shown are the ones the decision turns on. `worst_r2` leads
 * because an emulator misleads a calibration wherever it is weakest, not where it
 * is average, so the worst feature bounds what the bundle can be trusted for. The
 * median is beside it because one bad feature out of thirty-six is a different
 * situation from a bundle that is poor throughout, and the worst alone cannot
 * tell those apart.
 *
 * The directory name and the model type are both shown, and deliberately not
 * collapsed into one. They can disagree -- a refit that died after its metadata
 * was copied in leaves a directory named for one family reporting another -- and
 * a row that says `..._MLP` alongside `two_phase_RadialBasisFunctions` is the
 * only place that is visible.
 */
const props = defineProps({
  visible: { type: Boolean, default: false },
  /** Rows from the backend, already best-first. */
  choices: { type: Array, default: () => [] },
  /** Directory of the bundle currently being read. */
  currentDir: { type: String, default: '' },
  /** Whether `currentDir` is an explicit pick rather than the fallback. */
  chosen: { type: Boolean, default: false },
})
const emit = defineEmits(['update:visible', 'select'])

const picked = ref('')

watch(
  () => props.visible,
  (open) => {
    if (open) picked.value = props.currentDir || ''
  },
)

/** `0.9974` reads better than `0.997400000001`; a missing score is not zero. */
function score(value) {
  return value === null || value === undefined || Number.isNaN(value)
    ? '—'
    : Number(value).toFixed(4)
}

function samples(row) {
  if (!row.num_samples) return ''
  const stages = row.num_stages > 1 ? `, ${row.num_stages} stages` : ''
  return `${row.num_samples} samples${stages}`
}

/**
 * Whether the directory name names a family the metadata does not report.
 *
 * Only a hint, and treated as one: the directory name is whatever the person who
 * trained it chose, so a mismatch is not proof of anything. But the one case it
 * catches -- a half-written refit still holding the previous family's metadata --
 * is otherwise indistinguishable from a healthy bundle, and worth a second look.
 */
function suspicious(row) {
  const model = (row.model_name || '').replace(/^two_phase_/, '')
  const name = row.name || ''
  if (!model || !name) return false
  const known = ['RadialBasisFunctions', 'RBF', 'MLP', 'RandomForest', 'LightGBM',
                 'EnsembleMLP', 'GaussianProcess', 'PolynomialRegression',
                 'SupportVectorMachine']
  const claimed = known.find((k) => name.endsWith(k))
  if (!claimed) return false
  return !(model === claimed || (claimed === 'RBF' && model === 'RadialBasisFunctions'))
}

const anySuspicious = computed(() => props.choices.some((row) => suspicious(row)))

function isCurrent(row) {
  return row.dir === props.currentDir
}

function onUse() {
  emit('select', picked.value)
  emit('update:visible', false)
}
</script>

<template>
  <Dialog
    :visible="visible"
    modal
    header="Choose an emulator"
    :style="{ width: '46rem', maxWidth: '95vw' }"
    @update:visible="(v) => emit('update:visible', v)"
  >
    <p class="intro">
      This study has {{ choices.length }} trained emulators. They were fitted to the
      same design, so the difference between them is the emulator family, not the
      simulations.
    </p>
    <p v-if="!chosen && choices.length > 1" class="intro warn">
      No choice has been made, so the most recently written one is being used. That
      is not a reason to trust it — pick one below.
    </p>

    <div class="rows">
      <label v-for="row in choices" :key="row.dir" class="row" :class="{ picked: picked === row.dir }">
        <input v-model="picked" type="radio" :value="row.dir" />
        <span class="body">
          <span class="line1">
            <strong>{{ (row.model_name || row.name || '').replace(/^two_phase_/, '') || '—' }}</strong>
            <span v-if="(row.model_name || '').startsWith('two_phase_')" class="tag">two-phase</span>
            <span v-if="isCurrent(row)" class="tag current">in use</span>
            <span v-if="suspicious(row)" class="tag alert" title="The directory name names a different family from the metadata. A refit that died after its metadata was copied in looks exactly like this.">
              name/type mismatch
            </span>
          </span>
          <span class="line2">
            worst R²
            <strong :class="{ bad: row.worst_r2 !== null && row.worst_r2 < 0 }">{{ score(row.worst_r2) }}</strong>
            · median R² <strong>{{ score(row.median_r2) }}</strong>
            <template v-if="row.num_features"> · {{ row.num_features }} features</template>
            <template v-if="samples(row)"> · {{ samples(row) }}</template>
            <span v-if="!row.reusable" class="tag" title="No training_data.npz, so emulator_settings.reuse_samples has nothing to refit here.">not refittable</span>
          </span>
          <span class="line3">{{ row.name }}</span>
        </span>
      </label>
    </div>

    <p v-if="anySuspicious" class="intro warn">
      A row is flagged because its directory name and its reported model type
      disagree. That is what a refit interrupted partway through looks like — check
      it before calibrating against it.
    </p>

    <template #footer>
      <Button label="Cancel" text @click="emit('update:visible', false)" />
      <Button label="Use this emulator" :disabled="!picked" @click="onUse" />
    </template>
  </Dialog>
</template>

<style scoped>
.intro {
  margin: 0 0 0.75rem;
  font-size: 0.9rem;
  opacity: 0.85;
}
.intro.warn {
  opacity: 1;
  color: var(--p-orange-400, #d97706);
}
.rows {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.row {
  display: flex;
  gap: 0.6rem;
  align-items: flex-start;
  padding: 0.55rem 0.7rem;
  border: 1px solid var(--p-content-border-color, #d4d4d8);
  border-radius: 6px;
  cursor: pointer;
}
.row.picked {
  border-color: var(--p-primary-color, #3b82f6);
  background: var(--p-highlight-background, rgba(59, 130, 246, 0.08));
}
.body {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  min-width: 0;
}
.line1 {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-wrap: wrap;
}
.line2 {
  font-size: 0.85rem;
  opacity: 0.85;
}
.line3 {
  font-size: 0.78rem;
  opacity: 0.6;
  overflow-wrap: anywhere;
}
.tag {
  font-size: 0.7rem;
  padding: 0.05rem 0.35rem;
  border-radius: 4px;
  background: var(--p-content-border-color, #e4e4e7);
}
.tag.current {
  background: var(--p-primary-color, #3b82f6);
  color: #fff;
}
.tag.alert {
  background: var(--p-orange-400, #d97706);
  color: #fff;
}
.bad {
  color: var(--p-red-500, #ef4444);
}
</style>
