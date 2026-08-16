import { ref, computed } from 'vue'

/** Holds the uploaded model id, name and classified variable lists. */
export function useModel() {
  const modelId = ref(null)
  const name = ref(null)
  // Prefix of the uploaded .cellml filename (extension stripped), shown in the
  // top bar; falls back to the model name when no filename is available.
  const filePrefix = ref(null)
  const variables = ref({ params: [], odes: [], algebraic: [], all_names: [], units: {} })
  // What kind of model was uploaded, as the server reports it. Only an external
  // python model says anything here (`external_python`); a CellML or .mmt upload
  // carries no model_format, which is the empty default. It is the model's own
  // nature, not a setting — the backend to run it with follows from it.
  const modelFormat = ref('')
  // The name of the file the model was converted from, when it was converted at
  // import: a Myokit `.mmt` becomes CellML at the door (#27), so the model the
  // app runs is not the file the user wrote. Null for everything else. It is
  // what tells the Edit button to show the .mmt rather than open PhLynx, which
  // builds CellML and has nothing to say about a Myokit model.
  const convertedFrom = ref(null)

  function setModel({ model_id, name: modelName, filename, model_format, converted_from }) {
    modelId.value = model_id
    name.value = modelName
    filePrefix.value = filename ? filename.replace(/\.[^/.]+$/, '') : (modelName ?? null)
    modelFormat.value = model_format ?? ''
    convertedFrom.value = converted_from ?? null
  }

  function setVariables(vars) {
    variables.value = {
      params: vars.params ?? [],
      odes: vars.odes ?? [],
      algebraic: vars.algebraic ?? [],
      all_names: vars.all_names ?? [],
      initial_values: vars.initial_values ?? {},
      // qname -> CellML units identifier, used to annotate plot axes (#125).
      units: vars.units ?? {},
    }
  }

  const hasModel = computed(() => modelId.value !== null)
  const defaultOutputs = computed(() => variables.value.odes)

  return {
    modelId,
    name,
    filePrefix,
    modelFormat,
    convertedFrom,
    variables,
    setModel,
    setVariables,
    hasModel,
    defaultOutputs,
  }
}
