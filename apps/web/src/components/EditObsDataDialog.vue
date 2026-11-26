<script setup>
/**
 * "Edit obs_data": protocol-kit's obs_data editor (the same one PhLynx mounts) on a whole
 * obs_data document -- its protocol_info, data_items, prediction_items and
 * prediction_plots, and any key it does not know, which it keeps.
 *
 * By default it edits the open study's obs_data and saves a dated copy into the outputs
 * directory, which is then loaded. A host can instead give it a `document` and a `save`
 * (a workflow step's obs_data, say), and `preselect` parameters the protocol must set:
 * each gets a lane with no value yet, which the editor reports until it is filled in.
 */
import { ref, computed, watch } from 'vue'
import Dialog from 'primevue/dialog'
import Button from 'primevue/button'
import Message from 'primevue/message'
import {
  DATA_ITEM_VOCABULARY,
  readObsDataOptions,
  readObsDataParts,
  validateDataItems,
  validateProtocolInfo,
} from '@physiomelinks/protocol-kit'
import { ObsDataEditor } from '@physiomelinks/protocol-kit/editor'
import '@physiomelinks/protocol-kit/editor.css'
import { versionedJsonName, withParameters } from '../lib/obsDataJson'
import { errorMessage, getObsDataDocument, getObsDataOptions, uploadObsData } from '../lib/api'
import EditOperationFuncsDialog from './EditOperationFuncsDialog.vue'

const props = defineProps({
  visible: { type: Boolean, default: false },
  modelId: { type: String, default: null },
  // Model variables store slice: {params, odes, algebraic, all_names, initial_values, units}.
  modelVariables: { type: Object, default: () => ({}) },
  modelName: { type: String, default: null },
  loadedFilename: { type: String, default: null },
  // Where Save writes the dated copy (#215); '' falls back to the config dir.
  outputsDir: { type: String, default: '' },
  // The time between recorded samples, to check each prediction item's range takes some.
  dt: { type: Number, default: null },
  // A document to edit instead of the study's obs_data (undefined: the study's).
  document: { type: [Object, Array], default: undefined },
  // Saves the edited document instead of writing the study's dated copy: (document) => Promise.
  save: { type: Function, default: null },
  // Parameters the protocol must set (a workflow step's unset ones, `C/x`), given lanes.
  preselect: { type: Array, default: () => [] },
  header: { type: String, default: 'Edit obs_data' },
  hint: { type: String, default: '' },
})
const emit = defineEmits(['update:visible', 'saved'])

const doc = ref(null)
const vocabulary = ref(DATA_ITEM_VOCABULARY)
const loading = ref(false)
const saving = ref(false)
const error = ref('')
// "Custom funcs" authoring dialog (issue #58).
const opFuncsOpen = ref(false)

// Fetch the CA-sourced vocabulary (refresh=true re-introspects CA after a custom
// func is added, so it is offered).
async function loadOptions(refresh = false) {
  try {
    // The output dir locates the user's custom funcs.
    const options = await getObsDataOptions(refresh, localStorage.getItem('cuflynx-outputs-dir') || '')
    vocabulary.value = readObsDataOptions(options)
  } catch {
    /* keep the built-in vocabulary: the editor still works offline */
  }
}

watch(
  () => props.visible,
  async (visible) => {
    if (!visible) return
    error.value = ''
    loading.value = true
    try {
      await loadOptions()
      let start = props.document
      if (start === undefined && props.modelId) {
        try {
          start = await getObsDataDocument(props.modelId)
        } catch (e) {
          error.value = errorMessage(e)
        }
      }
      // An obs_data always has a protocol_info: a new one starts from one experiment.
      doc.value = withParameters(start ?? null, props.preselect)
    } finally {
      loading.value = false
    }
  },
  { immediate: true },
)

const KINDS = [
  ['params', 'constant'],
  ['odes', 'state'],
  ['algebraic', 'variable'],
]

/** The model's variables as the editor takes them, and the preselected parameters it may lack. */
const variables = computed(() => {
  const mv = props.modelVariables ?? {}
  const seen = new Set()
  const out = []
  for (const [key, kind] of KINDS) {
    for (const name of mv[key] ?? []) {
      if (seen.has(name)) continue
      seen.add(name)
      out.push({ name, kind, unit: mv.units?.[name], value: mv.initial_values?.[name] })
    }
  }
  for (const name of [...(mv.all_names ?? []), ...props.preselect]) {
    if (seen.has(name)) continue
    seen.add(name)
    out.push({
      name,
      kind: props.preselect.includes(name) ? 'constant' : 'variable',
      unit: mv.units?.[name],
    })
  }
  return out
})

function getValue(name) {
  return props.modelVariables?.initial_values?.[name]
}

/** What circulatory_autogen would refuse of the document; Save waits for none. */
const problems = computed(() => {
  if (!doc.value) return []
  const { protocolInfo } = readObsDataParts(doc.value)
  const protocol = protocolInfo ? validateProtocolInfo(protocolInfo).errors : []
  const items = validateDataItems(doc.value, { vocabulary: vocabulary.value }).errors ?? []
  return [...protocol, ...items]
})

const dataItemCount = computed(() =>
  doc.value ? (readObsDataParts(doc.value).dataItems?.length ?? 0) : 0,
)

const canSave = computed(
  () => !saving.value && !loading.value && !!doc.value && problems.value.length === 0,
)

async function onOpFuncsSaved() {
  await loadOptions(true)
}

async function onSave() {
  error.value = ''
  saving.value = true
  try {
    if (props.save) {
      emit('saved', await props.save(doc.value))
    } else {
      const filename = versionedJsonName(props.loadedFilename, props.modelName)
      // Written server-side into the output directory (#215), not downloaded.
      const summary = await uploadObsData(props.modelId, doc.value, {
        filename,
        outputsDir: props.outputsDir,
      })
      emit('saved', { ...summary, filename })
    }
    emit('update:visible', false)
  } catch (e) {
    error.value = errorMessage(e)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <Dialog
    :visible="visible"
    modal
    :header="header"
    :style="{ width: '64rem' }"
    data-testid="edit-obs"
    @update:visible="emit('update:visible', $event)"
  >
    <p class="eo-hint">
      <template v-if="hint">{{ hint }}</template>
      <template v-else>
        Edit the observation targets. Saving writes a new
        <code>…_yymmdd.json</code> to the output directory (the original is kept) and applies it.
      </template>
    </p>

    <Message v-if="error" severity="error" data-testid="eo-error" :closable="false">
      {{ error }}
    </Message>

    <div data-testid="eo-protocol">
      <ObsDataEditor
        v-if="doc"
        v-model:document="doc"
        :variables="variables"
        :get-value="getValue"
        :vocabulary="vocabulary"
        :dt="dt"
        preset="cuflynx"
      >
        <template #data-items-actions>
          <Button
            label="Custom funcs"
            icon="pi pi-code"
            size="small"
            text
            data-testid="eo-add-op-func"
            title="Write your own operation or cost function in Python"
            @click="opFuncsOpen = true"
          />
        </template>
      </ObsDataEditor>
    </div>

    <template #footer>
      <span class="eo-count">{{ dataItemCount }} data item(s)</span>
      <span v-if="problems.length" class="eo-blocked" data-testid="eo-problems">
        {{ problems.length }} problem(s) to fix before saving
      </span>
      <Button label="Cancel" size="small" text @click="emit('update:visible', false)" />
      <Button
        label="Save"
        size="small"
        :disabled="!canSave"
        :loading="saving"
        data-testid="eo-save"
        @click="onSave"
      />
    </template>

    <EditOperationFuncsDialog v-model:visible="opFuncsOpen" @saved="onOpFuncsSaved" />
  </Dialog>
</template>

<style scoped>
.eo-hint {
  font-size: 0.8rem;
  opacity: 0.75;
  margin: 0 0 0.5rem;
}
.eo-count {
  margin-right: auto;
  font-size: 0.8rem;
  opacity: 0.75;
}
.eo-blocked {
  font-size: 0.8rem;
  color: var(--p-red-500, #ef4444);
}
</style>
