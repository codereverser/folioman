<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import Dialog from 'primevue/dialog'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import { formatUnits } from '@/utils/format'
import type { IntegrityRow, ManualCorporateActionBody, SecurityOption } from '@/stores/integrity'
import {
  MANUAL_CA_KINDS,
  emptyManualCaForm,
  isCrossSecurityKind,
  isManualCaValid,
  isRightsOrBuybackKind,
  isUnitFactorKind,
  toManualCaBody,
  type ManualCaKind,
} from '@/integrity/manualCorporateAction'

const props = defineProps<{
  visible: boolean
  row: IntegrityRow | null
  loading: boolean
  initialKind?: ManualCaKind
  /** ``merger`` locks the form to a cross-ISIN amalgamation (ledger_position rows). */
  variant?: 'general' | 'merger'
  /** The investor's securities, for the acquirer picker (merger/demerger). */
  securities?: SecurityOption[]
}>()
const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'submit', body: ManualCorporateActionBody): void
}>()

const form = ref(emptyManualCaForm())
const dialogHeader = computed(() =>
  props.variant === 'merger' ? 'Resolve as merger' : 'Resolve with a corporate action',
)

watch(
  () => props.visible,
  (v) => {
    if (v) {
      const next = emptyManualCaForm(props.row?.snapshotAsOf ?? '')
      if (props.variant === 'merger') {
        next.kind = 'merger'
      } else if (props.initialKind) {
        next.kind = props.initialKind
      }
      form.value = next
      acquirerChoice.value = null
    }
  },
)

const isUnitFactor = computed(() => isUnitFactorKind(form.value.kind))
const isMerger = computed(() => form.value.kind === 'merger')
const isDemerger = computed(() => form.value.kind === 'demerger')
const isCrossSecurity = computed(() => isCrossSecurityKind(form.value.kind))
const isRightsOrBuyback = computed(() => isRightsOrBuybackKind(form.value.kind))
const valid = computed(() => isManualCaValid(form.value))

// Acquirer picker: the acquiring company is almost always already in the portfolio.
// Offer the investor's securities (minus the one merging away) plus a manual fallback.
const MANUAL = '__manual__'
const acquirerChoice = ref<number | typeof MANUAL | null>(null)
const _EQUITY_TYPES = new Set(['equity', 'etf', 'foreign_equity'])
const acquirerOptions = computed(() => {
  const opts = (props.securities ?? [])
    .filter((s) => s.id !== props.row?.securityId && _EQUITY_TYPES.has(s.security_type))
    .map((s) => ({ label: s.symbol ? `${s.name} (${s.symbol})` : s.name, value: s.id }))
  opts.push({ label: 'Other — enter ISIN manually', value: MANUAL })
  return opts
})
const acquirerIsManual = computed(() => acquirerChoice.value === MANUAL)

watch(acquirerChoice, (choice) => {
  if (choice === MANUAL) {
    form.value.cpIsin = ''
    form.value.cpSymbol = ''
    form.value.cpName = ''
    return
  }
  const sec = (props.securities ?? []).find((s) => s.id === choice)
  if (sec) {
    form.value.cpIsin = sec.isin
    form.value.cpSymbol = sec.symbol
    form.value.cpName = sec.name
  }
})

function submit(): void {
  if (!valid.value) return
  emit('submit', toManualCaBody(form.value))
}
</script>

<template>
  <Dialog
    :visible="visible"
    :header="dialogHeader"
    modal
    :style="{ width: '30rem' }"
    @update:visible="emit('update:visible', $event)"
  >
    <p v-if="row && variant === 'merger'" class="dialog-copy">
      Your tradebook shows <strong>{{ formatUnits(row.unitsFromTransactions) }}</strong> units of
      <strong>{{ row.name }}</strong> (<span class="mono">{{ row.isin }}</span>), but that ISIN no
      longer appears on the eCAS holdings. Enter the <em>acquiring</em> company's ISIN and the
      exchange ratio (new shares per old share). Example: HDFC → HDFCBANK at 42:25 is
      <code>1.68</code>.
    </p>
    <p v-else-if="row" class="dialog-copy">
      <strong>{{ row.name }}</strong> — your trades net to
      <strong>{{ formatUnits(row.unitsFromTransactions) }}</strong> units, but the demat statement
      shows <strong>{{ formatUnits(row.unitsFromHoldings) }}</strong
      >. Record the corporate action that explains the gap; we re-run the ledger and reconcile.
    </p>

    <div class="dialog-form">
      <label v-if="variant !== 'merger'">
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
        New shares per old share
        <InputText v-model="form.mergerRatio" inputmode="decimal" placeholder="e.g. 1.68" />
        <small v-if="variant === 'merger'" class="hint">
          Multiply held units by this ratio — 42 new for every 25 old is
          <code>1.68</code> (enter as a decimal, not 42:25).
        </small>
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
          Acquiring company
          <Select
            v-model="acquirerChoice"
            :options="acquirerOptions"
            option-label="label"
            option-value="value"
            placeholder="Pick from your portfolio"
          />
        </label>
        <template v-if="acquirerIsManual">
          <label>
            Acquiring company ISIN
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
.mono {
  font-family: var(--fm-font-mono);
  font-size: 0.8125rem;
}
</style>
