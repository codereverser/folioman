<script setup lang="ts">
import { computed, onMounted, reactive } from 'vue'
import { useRouter } from 'vue-router'
import Panel from 'primevue/panel'
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import Checkbox from 'primevue/checkbox'
import Tag from 'primevue/tag'
import { useConfirm } from 'primevue/useconfirm'
import { useRosterStore, type RosterFamily, type RosterInvestor, type RosterGroup } from '@/stores/roster'
import { useFamilyStore } from '@/stores/family'
import { useInvestorStore } from '@/stores/investor'
import { useUiStore } from '@/stores/ui'
import { useRosterMetrics } from '@/composables/useRosterMetrics'
import { formatInr, formatDate, toNumber } from '@/utils/format'

const router = useRouter()
const roster = useRosterStore()
const familyStore = useFamilyStore()
const investorStore = useInvestorStore()
const ui = useUiStore()
const confirm = useConfirm()
const metrics = useRosterMetrics()

const RELATIONS = ['Self', 'Spouse', 'Child', 'Parent', 'Sibling', 'HUF', 'Other']

// "Move to family" options: every family plus an Unaffiliated (null) bucket.
const familyOptions = computed(() => [
  { label: roster.UNAFFILIATED_LABEL, value: null as number | null },
  ...roster.families.map((f) => ({ label: f.name, value: f.id as number | null })),
])

onMounted(() => void roster.ensureLoaded())

// --- navigation -------------------------------------------------------------
function openInvestor(investorId: number): void {
  ui.selectInvestor(investorId)
  void router.push({ name: 'dashboard', params: { investorId } })
}
function openFamily(familyId: number): void {
  ui.selectFamily(familyId)
  void router.push({ name: 'family', params: { familyId } })
}

// Lazily load roll-ups when a group is expanded (panel toggled open).
function onGroupToggle(group: RosterGroup, collapsed: boolean): void {
  if (collapsed) return
  if (group.family) void metrics.loadFamilyAggregate(group.family.id)
  for (const inv of group.investors) void metrics.loadInvestorSummary(inv.id)
}

// --- family dialog ----------------------------------------------------------
const familyForm = reactive<{ visible: boolean; mode: 'create' | 'rename'; id: number | null; name: string }>({
  visible: false,
  mode: 'create',
  id: null,
  name: '',
})
function openCreateFamily(): void {
  Object.assign(familyForm, { visible: true, mode: 'create', id: null, name: '' })
}
function openRenameFamily(family: RosterFamily): void {
  Object.assign(familyForm, { visible: true, mode: 'rename', id: family.id, name: family.name })
}
async function saveFamily(): Promise<void> {
  const name = familyForm.name.trim()
  if (!name) return
  const ok =
    familyForm.mode === 'create'
      ? await familyStore.createFamily(name)
      : await familyStore.renameFamily(familyForm.id as number, name)
  if (ok) {
    familyForm.visible = false
    ui.notify({ severity: 'success', summary: familyForm.mode === 'create' ? 'Family created' : 'Family renamed' })
  }
}
function confirmDeleteFamily(group: RosterGroup): void {
  const family = group.family
  if (!family) return
  const n = group.investors.length
  confirm.require({
    header: 'Delete family',
    message: `Delete "${family.name}"?${n ? ` Its ${n} investor${n > 1 ? 's' : ''} move to ${roster.UNAFFILIATED_LABEL}.` : ''}`,
    icon: 'pi pi-exclamation-triangle',
    rejectProps: { label: 'Cancel', severity: 'secondary', outlined: true },
    acceptProps: { label: 'Delete', severity: 'danger' },
    accept: async () => {
      if (await familyStore.deleteFamily(family.id)) {
        ui.notify({ severity: 'success', summary: 'Family deleted' })
      }
    },
  })
}

// --- investor dialog --------------------------------------------------------
const investorForm = reactive<{
  visible: boolean
  mode: 'create' | 'edit'
  id: number | null
  name: string
  relation: string
  isHuf: boolean
  familyId: number | null
  pan: string
}>({
  visible: false,
  mode: 'create',
  id: null,
  name: '',
  relation: 'Self',
  isHuf: false,
  familyId: null,
  pan: '',
})

// Indian PAN: five letters, four digits, one letter (e.g. ABCDE1234F).
const PAN_PATTERN = /^[A-Z]{5}[0-9]{4}[A-Z]$/
const panError = computed(() => {
  const value = investorForm.pan.trim()
  return value && !PAN_PATTERN.test(value) ? 'PAN must look like ABCDE1234F' : ''
})

function openCreateInvestor(familyId: number | null = null): void {
  Object.assign(investorForm, {
    visible: true,
    mode: 'create',
    id: null,
    name: '',
    relation: 'Self',
    isHuf: false,
    familyId,
    pan: '',
  })
}
function openEditInvestor(inv: RosterInvestor): void {
  Object.assign(investorForm, {
    visible: true,
    mode: 'edit',
    id: inv.id,
    name: inv.name,
    relation: 'Self',
    isHuf: false,
    familyId: inv.familyId,
    // The API never returns a stored PAN; blank means "leave unchanged" on save.
    pan: '',
  })
}
async function saveInvestor(): Promise<void> {
  const name = investorForm.name.trim()
  if (!name || panError.value) return
  const pan = investorForm.pan.trim().toUpperCase()
  const fields = {
    name,
    relation: investorForm.relation,
    is_huf: investorForm.isHuf,
    family_id: investorForm.familyId,
  }
  const ok =
    investorForm.mode === 'create'
      ? // PAN is optional on create; omit it entirely when blank.
        await investorStore.createInvestor({ ...fields, email: '', ...(pan ? { pan } : {}) })
      : // On edit a blank PAN field means "leave unchanged" — only PATCH it when set.
        await investorStore.updateInvestor(investorForm.id as number, {
          ...fields,
          ...(pan ? { pan } : {}),
        })
  if (ok) {
    investorForm.visible = false
    ui.notify({ severity: 'success', summary: investorForm.mode === 'create' ? 'Investor added' : 'Investor updated' })
  }
}
function confirmDeleteInvestor(inv: RosterInvestor): void {
  confirm.require({
    header: 'Delete investor',
    message: `Delete "${inv.name}"? This removes their folios, holdings and transactions.`,
    icon: 'pi pi-exclamation-triangle',
    rejectProps: { label: 'Cancel', severity: 'secondary', outlined: true },
    acceptProps: { label: 'Delete', severity: 'danger' },
    accept: async () => {
      if (await investorStore.deleteInvestor(inv.id)) {
        ui.notify({ severity: 'success', summary: 'Investor deleted' })
      }
    },
  })
}

async function moveInvestor(inv: RosterInvestor, familyId: number | null): Promise<void> {
  if (familyId === inv.familyId) return
  await investorStore.setFamily(inv.id, familyId)
}

// Honest value state: a held investor whose value computes to 0 isn't worth ₹0 —
// it's not priced yet (valuation running, or snapshot-only holdings with no live
// price). Show that instead of a misleading zero. A genuinely empty roster entry
// (no holdings) keeps the dash.
function isValuePending(inv: RosterInvestor): boolean {
  const s = metrics.investorSummaries.value[inv.id]
  return !!s && s.holdingsCount > 0 && toNumber(s.totalInr) <= 0
}
function investorValue(inv: RosterInvestor): string {
  const s = metrics.investorSummaries.value[inv.id]
  if (!s) return '—'
  return isValuePending(inv) ? 'Valuation pending' : formatInr(s.totalInr)
}
</script>

<template>
  <section class="roster">
    <header class="page-head">
      <div>
        <h1>Investors</h1>
        <p v-if="roster.usingSeed" class="demo-hint">
          <i class="pi pi-info-circle" /> Showing demo data — no backend connected.
        </p>
      </div>
      <div v-if="!ui.isMobile" class="actions">
        <Button label="New family" icon="pi pi-sitemap" severity="secondary" outlined size="small" @click="openCreateFamily" />
        <Button label="New investor" icon="pi pi-user-plus" size="small" @click="openCreateInvestor()" />
      </div>
    </header>

    <p v-if="roster.loading && !roster.loaded" class="muted">Loading roster…</p>

    <div v-else-if="roster.isEmpty" class="empty">
      <i class="pi pi-inbox" />
      <p>No investors yet.</p>
      <p class="muted">Add an investor, then import a CAS to get started.</p>
      <Button v-if="!ui.isMobile" label="Add investor" icon="pi pi-user-plus" @click="openCreateInvestor()" />
    </div>

    <div v-else class="groups">
      <Panel
        v-for="group in roster.groups"
        :key="group.family?.id ?? 'unaffiliated'"
        toggleable
        :collapsed="true"
        @toggle="(e) => onGroupToggle(group, e.value)"
      >
        <template #header>
          <div class="group-header">
            <button v-if="group.family" type="button" class="family-name is-link" @click.stop="openFamily(group.family.id)">
              {{ group.family.name }}
            </button>
            <span v-else class="family-name">{{ roster.UNAFFILIATED_LABEL }}</span>
            <Tag :value="`${group.investors.length}`" rounded severity="secondary" />
            <span v-if="group.family && metrics.familyAggregates.value[group.family.id]" class="combined">
              {{ formatInr(metrics.familyAggregates.value[group.family.id].totalInr) }} combined
            </span>
          </div>
        </template>
        <template v-if="group.family && !ui.isMobile" #icons>
          <Button icon="pi pi-pencil" text rounded size="small" aria-label="Rename family" @click.stop="openRenameFamily(group.family)" />
          <Button icon="pi pi-trash" text rounded size="small" severity="danger" aria-label="Delete family" @click.stop="confirmDeleteFamily(group)" />
          <Button icon="pi pi-user-plus" text rounded size="small" aria-label="Add investor to family" @click.stop="openCreateInvestor(group.family.id)" />
        </template>

        <ul class="investors">
          <li v-for="inv in group.investors" :key="inv.id" class="investor-row">
            <button type="button" class="inv-name is-link" @click="openInvestor(inv.id)">{{ inv.name }}</button>
            <span class="metric value" :class="{ pending: isValuePending(inv) }">{{ investorValue(inv) }}</span>
            <span class="metric tax-ready" :class="{ attention: (metrics.investorSummaries.value[inv.id]?.needsAttentionCount ?? 0) > 0 }">
              <template v-if="metrics.investorSummaries.value[inv.id]">
                {{ metrics.investorSummaries.value[inv.id].taxReadyCount }}/{{ metrics.investorSummaries.value[inv.id].integrityUnitCount }} tax-ready
              </template>
              <template v-else>—</template>
            </span>
            <span class="metric last-import muted">{{ formatDate(metrics.investorSummaries.value[inv.id]?.lastImportAt) }}</span>
            <div v-if="!ui.isMobile" class="row-actions">
              <Select
                :model-value="inv.familyId"
                :options="familyOptions"
                option-label="label"
                option-value="value"
                size="small"
                class="move-select"
                @update:model-value="(v) => moveInvestor(inv, v)"
              />
              <Button icon="pi pi-pencil" text rounded size="small" aria-label="Edit investor" @click="openEditInvestor(inv)" />
              <Button icon="pi pi-trash" text rounded size="small" severity="danger" aria-label="Delete investor" @click="confirmDeleteInvestor(inv)" />
            </div>
          </li>
          <li v-if="group.investors.length === 0" class="muted empty-group">No investors in this family.</li>
        </ul>
      </Panel>
    </div>

    <!-- Family create / rename -->
    <Dialog v-model:visible="familyForm.visible" modal :header="familyForm.mode === 'create' ? 'New family' : 'Rename family'" :style="{ width: '24rem' }">
      <div class="field">
        <label for="family-name">Family name</label>
        <InputText id="family-name" v-model="familyForm.name" autofocus @keyup.enter="saveFamily" />
      </div>
      <template #footer>
        <Button label="Cancel" severity="secondary" outlined @click="familyForm.visible = false" />
        <Button label="Save" :loading="familyStore.saving" :disabled="!familyForm.name.trim()" @click="saveFamily" />
      </template>
    </Dialog>

    <!-- Investor create / edit -->
    <Dialog v-model:visible="investorForm.visible" modal :header="investorForm.mode === 'create' ? 'New investor' : 'Edit investor'" :style="{ width: '26rem' }">
      <div class="field">
        <label for="inv-name">Name</label>
        <InputText id="inv-name" v-model="investorForm.name" autofocus />
      </div>
      <div class="field">
        <label for="inv-relation">Relation</label>
        <Select id="inv-relation" v-model="investorForm.relation" :options="RELATIONS" editable />
      </div>
      <div class="field">
        <label for="inv-pan">PAN <span class="optional">(optional)</span></label>
        <InputText
          id="inv-pan"
          v-model="investorForm.pan"
          :invalid="!!panError"
          maxlength="10"
          placeholder="ABCDE1234F"
          style="text-transform: uppercase"
          @input="investorForm.pan = investorForm.pan.toUpperCase()"
        />
        <small v-if="panError" class="field-error">{{ panError }}</small>
        <small v-else class="field-hint">
          {{ investorForm.mode === 'edit' ? 'Leave blank to keep the existing PAN. ' : '' }}Needed
          for the per-PAN capital-gains (Schedule 112A) export.
        </small>
      </div>
      <div class="field">
        <label for="inv-family">Family</label>
        <Select id="inv-family" v-model="investorForm.familyId" :options="familyOptions" option-label="label" option-value="value" />
      </div>
      <div class="field-inline">
        <Checkbox v-model="investorForm.isHuf" input-id="inv-huf" binary />
        <label for="inv-huf">This is a HUF entity</label>
      </div>
      <template #footer>
        <Button label="Cancel" severity="secondary" outlined @click="investorForm.visible = false" />
        <Button label="Save" :loading="investorStore.saving" :disabled="!investorForm.name.trim() || !!panError" @click="saveInvestor" />
      </template>
    </Dialog>
  </section>
</template>

<style scoped>
.roster {
  padding: var(--fm-space-6);
  max-width: var(--fm-content-max);
  margin: 0 auto;
  width: 100%;
}

.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--fm-space-4);
  margin-bottom: var(--fm-space-5);
}
.page-head h1 {
  margin: 0;
  font-size: 1.5rem;
  font-weight: 600;
}
.actions {
  display: flex;
  gap: var(--fm-space-2);
}

.muted {
  color: var(--fm-text-muted);
}

.demo-hint {
  display: inline-flex;
  align-items: center;
  gap: var(--fm-space-2);
  color: var(--fm-text-muted);
  font-size: 0.8125rem;
  margin: 0.25rem 0 0;
}

.empty {
  text-align: center;
  padding: var(--fm-space-12) var(--fm-space-4);
}
.empty .pi {
  font-size: 2rem;
  color: var(--fm-text-subtle);
}
.empty p {
  margin: var(--fm-space-2) 0 var(--fm-space-4);
}

.groups {
  display: flex;
  flex-direction: column;
  gap: var(--fm-space-4);
}

.group-header {
  display: flex;
  align-items: center;
  gap: var(--fm-space-3);
}
.family-name {
  font-weight: 600;
  font-size: 1rem;
}
.combined {
  color: var(--fm-text-muted);
  font-size: 0.8125rem;
}

.investors {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}
.investor-row {
  display: flex;
  align-items: center;
  gap: var(--fm-space-3);
  padding: var(--fm-space-2) 0;
  border-bottom: 1px solid var(--fm-border-subtle);
}
.investor-row:last-child {
  border-bottom: none;
}
.inv-name {
  flex: 1;
  text-align: left;
  font-weight: 500;
}
.metric {
  font-size: 0.8125rem;
  white-space: nowrap;
}
.value {
  min-width: 7rem;
  text-align: right;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}
/* "Valuation pending" — not a real number, so de-emphasise it (never a hard ₹0). */
.value.pending {
  color: var(--fm-text-muted);
  font-weight: 500;
  font-size: 0.8125rem;
}
.tax-ready {
  min-width: 8rem;
  color: var(--fm-text-muted);
}
.tax-ready.attention {
  color: var(--fm-critical);
  font-weight: 500;
}
.last-import {
  min-width: 6.5rem;
}
.row-actions {
  display: flex;
  align-items: center;
  gap: var(--fm-space-1);
}
.move-select {
  min-width: 11rem;
}
.empty-group {
  padding: var(--fm-space-2) 0;
  font-size: 0.875rem;
}

.is-link {
  background: none;
  border: none;
  padding: 0;
  font: inherit;
  color: var(--fm-verified);
  cursor: pointer;
}
.is-link:hover {
  text-decoration: underline;
}

.field {
  display: flex;
  flex-direction: column;
  gap: var(--fm-space-2);
  margin-bottom: var(--fm-space-4);
}
.field label {
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--fm-text-muted);
}
.field-inline {
  display: flex;
  align-items: center;
  gap: var(--fm-space-2);
}
.field .optional {
  font-weight: 400;
  color: var(--fm-text-subtle, var(--fm-text-muted));
}
.field-hint {
  font-size: 0.75rem;
  color: var(--fm-text-muted);
}
.field-error {
  font-size: 0.75rem;
  color: var(--fm-danger, #d32f2f);
}

/* Mobile: each investor becomes a stacked card; metrics wrap below the name.
   CRUD/move controls are already hidden (isMobile), so rows stay tappable. */
@media (max-width: 768px) {
  .investor-row {
    flex-direction: column;
    align-items: stretch;
    gap: var(--fm-space-1);
    padding: var(--fm-space-3) 0;
  }
  .inv-name {
    flex: none;
    min-height: 44px;
    display: flex;
    align-items: center;
    font-size: 1rem;
  }
  .metric {
    min-width: 0;
    text-align: left;
  }
  .value {
    text-align: left;
    font-size: 1rem;
  }
}
</style>
