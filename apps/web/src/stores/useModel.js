import { ref, computed } from 'vue'

/** Holds the uploaded model id, name and classified variable lists. */
export function useModel() {
  const modelId = ref(null)
  const name = ref(null)
  const variables = ref({ params: [], odes: [], algebraic: [], all_names: [] })

  function setModel({ model_id, name: modelName }) {
    modelId.value = model_id
    name.value = modelName
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

  return { modelId, name, variables, setModel, setVariables, hasModel, defaultOutputs }
}
