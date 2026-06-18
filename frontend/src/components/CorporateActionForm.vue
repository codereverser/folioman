<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import Dialog from 'primevue/dialog'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import { formatUnits } from '@/utils/format'
import type { IntegrityRow, ManualCorporateActionBody } from '@/stores/integrity'
import {
  MANUAL_CA_KINDS,
  emptyManualCaForm,
  isCrossSecurityKind,
  isManualCaValid,
  isRightsOrBuybackKind,
  isUnitFactorKind,
  toManualCaBody,
} from '@/integrity/manualCorporateAction'

const props = defineProps<{ visible: boolean; row: IntegrityRow | null; loading: boolean }>()
const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'submit', body: ManualCorporateActionBody): void
}>()

const form = ref(emptyManualCaForm())
watch(
  () => props.visible,
  (v) => {
    if (v) form.value = emptyManualCaForm(props.row?.snapshotAsOf ?? '')
  },
)

const isUnitFactor = computed(() => isUnitFactorKind(form.value.kind))
const isMerger = computed(() => form.value.kind === 'merger')
const isDemerger = computed(() => form.value.kind === 'demerger')
const isCrossSecurity = computed(() => isCrossSecurityKind(form.value.kind))
const isRightsOrBuyback = computed(() => isRightsOrBuybackKind(form.value.kind))
const valid = computed(() => isManualCaValid(form.value))

function submit(): void {
  if (!valid.value) return
  emit('submit', toManualCaBody(form.value))
}
</script>

<template>
  <Dialog
    :visible="visible"
    header="Resolve with a corporate action"
    modal
    :style="{ width: '30rem' }"
    @update:visible="emit('update:visible', $event)"
  >
    <p v-if="row" class="dialog-copy">
      <strong>{{ row.name }}</strong> — your trades net to
      <strong>{{ formatUnits(row.unitsFromTransactions) }}</strong> units, but the demat statement
      shows <strong>{{ formatUnits(row.unitsFromHoldings) }}</strong
      >. Record the corporate action that explains the gap; we re-run the ledger and reconcile.
    </p>

    <div class="dialog-form">
      <label>
        Action
        <Select
          v-model="form.kind"
          :options="MANUAL_CA_KINDS"
          option-label="label"
          option-value="value"
        />
      </label>
      <label>
        Ex / effective date
        <InputText v-model="form.exDate" type="date" />
      </label>

      <label v-if="isUnitFactor">
        Unit multiplier
        <InputText v-model="form.unitMultiplier" inputmode="decimal" placeholder="e.g. 2" />
        <small class="hint">
          Resulting units ÷ held: a 1:1 bonus or 1→2 split = <code>2</code>; a 3:1 bonus =
          <code>4</code>; a reverse split uses a value below 1 (1→0.1 = <code>0.1</code>).
        </small>
      </label>

      <label v-if="isMerger">
        New share ratio (new per old)
        <InputText v-model="form.mergerRatio" inputmode="decimal" placeholder="e.g. 1.5" />
      </label>
      <template v-if="isDemerger">
        <label>
          Child shares per parent share
          <InputText v-model="form.childRatio" inputmode="decimal" placeholder="e.g. 1" />
        </label>
        <label>
          Fraction of cost moving to the child (0–1)
          <InputText v-model="form.childCostFraction" inputmode="decimal" placeholder="e.g. 0.4" />
        </label>
      </template>
      <template v-if="isCrossSecurity">
        <label>
          {{ isMerger ? 'Acquiring' : 'Child' }} security ISIN
          <InputText v-model="form.cpIsin" placeholder="INE…" />
        </label>
        <label>
          Symbol (optional)
          <InputText v-model="form.cpSymbol" placeholder="e.g. NEWCO" />
        </label>
        <label>
          Name (optional)
          <InputText v-model="form.cpName" />
        </label>
      </template>

      <template v-if="isRightsOrBuyback">
        <label>
          Units
          <InputText v-model="form.units" inputmode="decimal" />
        </label>
        <label>
          Price per unit
          <InputText v-model="form.price" inputmode="decimal" />
        </label>
      </template>
    </div>

    <template #footer>
      <Button label="Cancel" text @click="emit('update:visible', false)" />
      <Button label="Apply & reconcile" :loading="loading" :disabled="!valid" @click="submit" />
    </template>
  </Dialog>
</template>

<style scoped>
.dialog-copy {
  margin: 0 0 1rem;
  color: var(--fm-text-muted);
  font-size: 0.875rem;
}
.dialog-form {
  display: flex;
  flex-direction: column;
  gap: 0.85rem;
}
.dialog-form label {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  font-size: 0.8125rem;
  font-weight: 500;
}
.hint {
  font-weight: 400;
  color: var(--fm-text-subtle);
  line-height: 1.4;
}
.hint code {
  font-family: var(--fm-font-mono);
}
</style>
