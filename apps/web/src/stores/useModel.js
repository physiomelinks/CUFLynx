import { ref, computed } from 'vue'

/** Holds the uploaded model id, name and classified variable lists. */
export function useModel() {
  const modelId = ref(null)
  const name = ref(null)
  // Prefix of the uploaded .cellml filename (extension stripped), shown in the
  // top bar; falls back to the model name when no filename is available.
  const filePrefix = ref(null)
  const variables = ref({ params: [], odes: [], algebraic: [], all_names: [] })

  function setModel({ model_id, name: modelName, filename }) {
    modelId.value = model_id
    name.value = modelName
    filePrefix.value = filename ? filename.replace(/\.[^/.]+$/, '') : (modelName ?? null)
  }

  function setVariables(vars) {
    variables.value = {
      params: vars.params ?? [],
      odes: vars.odes ?? [],
      algebraic: vars.algebraic ?? [],
      all_names: vars.all_names ?? [],
      initial_values: vars.initial_values ?? {},
    }
  }

  const hasModel = computed(() => modelId.value !== null)
  const defaultOutputs = computed(() => variables.value.odes)

  return {
    modelId,
    name,
    filePrefix,
    variables,
    setModel,
    setVariables,
    hasModel,
    defaultOutputs,
  }
}
