<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import ToggleButton from 'primevue/togglebutton'
import IconField from 'primevue/iconfield'
import InputIcon from 'primevue/inputicon'
import Menu from 'primevue/menu'
import type { MenuItem } from 'primevue/menuitem'
import { useConfirm } from 'primevue/useconfirm'
import { useRosterStore, type RosterFamily, type RosterInvestor } from '@/stores/roster'
import { useFamilyStore } from '@/stores/family'
import { useInvestorStore } from '@/stores/investor'
import { useUiStore } from '@/stores/ui'
import { useRosterMetrics, type InvestorSummary } from '@/composables/useRosterMetrics'
import { useWriteLock } from '@/composables/useWriteLock'
import { api } from '@/api/client'
import { formatInr, formatDate, toNumber } from '@/utils/format'

const router = useRouter()
const roster = useRosterStore()
const familyStore = useFamilyStore()
const investorStore = useInvestorStore()
const ui = useUiStore()
const confirm = useConfirm()
const metrics = useRosterMetrics()
const { readOnly } = useWriteLock()

const SOLO_GROUP = roster.UNAFFILIATED_LABEL // "Individual investors"

const familyOptions = computed(() => [
  { label: SOLO_GROUP, value: null as number | null },
  ...roster.families.map((f) => ({ label: f.name, value: f.id as number | null })),
])

const agg = computed(() => metrics.rosterAggregate.value)
const hasFamilies = computed(() => roster.families.length > 0)

// Group by family by default when families exist; advisors can flatten to a pure
// searchable/sortable list.
const grouped = ref(false)
const filterText = ref('')

onMounted(async () => {
  // One call: the roster aggregate carries the header *and* a lean row per investor
  // (value read from the persisted InvestorValue), so there's no per-investor
  // /summary fan-out.
  void metrics.loadRosterAggregate()
  // Refetch the roster on every visit (not ensureLoaded's once-only) so an investor
  // created/changed elsewhere — e.g. a just-completed CAS import — shows up here.
  await roster.reload()
  grouped.value = hasFamilies.value
  for (const f of roster.families) void metrics.loadFamilyAggregate(f.id)
})

// --- rows -------------------------------------------------------------------
interface Row {
  id: number
  name: string
  hasPan: boolean
  panLocked: boolean
  familyId: number | null
  familyName: string
  valueNum: number
  summary: InvestorSummary | undefined
}
const rows = computed<Row[]>(() => {
  const list = roster.investors.map((inv): Row => {
    const summary = metrics.investorSummaries.value[inv.id]
    return {
      id: inv.id,
      name: inv.name,
      hasPan: inv.hasPan,
      panLocked: inv.panLocked,
      familyId: inv.familyId,
      familyName: inv.familyId != null ? (roster.familyName(inv.familyId) ?? 'Family') : SOLO_GROUP,
      valueNum: summary ? toNumber(summary.totalInr) : -1,
      summary,
    }
  })
  // Sort by family (group field) then value desc; in grouped mode DataTable groups
  // by familyName and the stable order keeps members value-sorted within a family.
  return list.sort((a, b) => a.familyName.localeCompare(b.familyName) || b.valueNum - a.valueNum)
})

const filters = computed(() => ({
  global: { value: filterText.value || null, matchMode: 'contains' },
}))

function rosterInvestor(row: Row): RosterInvestor {
  return {
    id: row.id,
    name: row.name,
    familyId: row.familyId,
    hasPan: row.hasPan,
    panLocked: row.panLocked,
  }
}

// --- navigation -------------------------------------------------------------
function openInvestor(id: number): void {
  ui.selectInvestor(id)
  void router.push({ name: 'dashboard', params: { investorId: id } })
}
function openFamily(familyId: number): void {
  ui.selectFamily(familyId)
  void router.push({ name: 'family', params: { familyId } })
}
// The preferred way to add an investor: a CAS import creates them automatically
// from the statement (by PAN), with real holdings — vs. an empty manual record.
function goImport(): void {
  void router.push({ name: 'import' })
}

// --- per-row metrics --------------------------------------------------------
function isPending(s?: InvestorSummary): boolean {
  return !!s && s.holdingsCount > 0 && toNumber(s.totalInr) <= 0
}
function valueText(s?: InvestorSummary): string {
  if (!s) return '—'
  return isPending(s) ? 'Valuation pending' : formatInr(s.totalInr)
}
function unpriced(s?: InvestorSummary): number {
  return s?.unpricedFundCount ?? 0
}
function integrityTone(s?: InvestorSummary): 'attention' | 'ok' | 'snapshot' | 'none' {
  if (!s || s.integrityUnitCount === 0) return 'none'
  if (s.needsAttentionCount > 0) return 'attention'
  if (s.taxReadyCount === s.integrityUnitCount) return 'ok'
  if (s.snapshotCount > 0) return 'snapshot'
  return 'none'
}
function integrityTitle(s?: InvestorSummary): string {
  if (!s) return ''
  return (
    `${s.taxReadyCount} of ${s.integrityUnitCount} holdings tax-ready` +
    (s.needsAttentionCount > 0 ? ` · ${s.needsAttentionCount} need attention` : '')
  )
}

// --- family-group header helpers -------------------------------------------
function familyIdFor(name: string): number | null {
  return roster.families.find((f) => f.name === name)?.id ?? null
}
function familyCombined(name: string): string | null {
  const id = familyIdFor(name)
  const a = id != null ? metrics.familyAggregates.value[id] : undefined
  return a ? formatInr(a.totalInr) : null
}
function familyNeedsAttention(name: string): boolean {
  const id = familyIdFor(name)
  const a = id != null ? metrics.familyAggregates.value[id] : undefined
  return !!a && a.needsAttentionCount > 0
}
function memberCount(name: string): number {
  return rows.value.filter((r) => r.familyName === name).length
}

// --- overflow menus ---------------------------------------------------------
const rowMenu = ref<InstanceType<typeof Menu> | null>(null)
const rowMenuModel = ref<MenuItem[]>([])
function openInvestorMenu(e: Event, row: Row): void {
  const inv = rosterInvestor(row)
  rowMenuModel.value = [
    {
      label: 'Edit',
      icon: 'pi pi-pencil',
      disabled: readOnly.value,
      command: () => openEditInvestor(inv),
    },
    {
      label: 'Delete',
      icon: 'pi pi-trash',
      disabled: readOnly.value,
      command: () => confirmDeleteInvestor(inv),
    },
  ]
  rowMenu.value?.toggle(e)
}
function openFamilyMenu(e: Event, name: string): void {
  const id = familyIdFor(name)
  if (id == null) return
  const family: RosterFamily = { id, name }
  rowMenuModel.value = [
    { label: 'Open family dashboard', icon: 'pi pi-chart-line', command: () => openFamily(id) },
    {
      label: 'Add investor',
      icon: 'pi pi-user-plus',
      disabled: readOnly.value,
      command: () => openCreateInvestor(id),
    },
    {
      label: 'Rename family',
      icon: 'pi pi-pencil',
      disabled: readOnly.value,
      command: () => openRenameFamily(family),
    },
    {
      label: 'Delete family',
      icon: 'pi pi-trash',
      disabled: readOnly.value,
      command: () => confirmDeleteFamily(family),
    },
  ]
  rowMenu.value?.toggle(e)
}

// --- family dialog ----------------------------------------------------------
const familyForm = reactive<{
  visible: boolean
  mode: 'create' | 'rename'
  id: number | null
  name: string
}>({ visible: false, mode: 'create', id: null, name: '' })
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
    ui.notify({
      severity: 'success',
      summary: familyForm.mode === 'create' ? 'Family created' : 'Family renamed',
    })
  }
}
function confirmDeleteFamily(family: RosterFamily): void {
  const n = memberCount(family.name)
  confirm.require({
    header: 'Delete family',
    message: `Delete "${family.name}"?${n ? ` Its ${n} investor${n > 1 ? 's' : ''} move to ${SOLO_GROUP}.` : ''}`,
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
  familyId: number | null
  pan: string
  hasPan: boolean
  panLocked: boolean
  panMasked: string
}>({
  visible: false,
  mode: 'create',
  id: null,
  name: '',
  familyId: null,
  pan: '',
  hasPan: false,
  panLocked: false,
  panMasked: '',
})

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
    familyId,
    pan: '',
    hasPan: false,
    panLocked: false,
    panMasked: '',
  })
}
async function openEditInvestor(inv: RosterInvestor): Promise<void> {
  Object.assign(investorForm, {
    visible: true,
    mode: 'edit',
    id: inv.id,
    name: inv.name,
    familyId: inv.familyId,
    pan: '',
    hasPan: inv.hasPan,
    panLocked: inv.panLocked,
    panMasked: '',
  })
  // Fetch the masked PAN (single-investor read; the full value is never returned)
  // so the dialog can disambiguate similar names and confirm what's on file.
  const { data } = await api.GET('/api/investors/{investor_id}', {
    params: { path: { investor_id: inv.id } },
  })
  if (data && investorForm.id === inv.id) {
    investorForm.hasPan = data.has_pan
    investorForm.panLocked = data.pan_locked
    investorForm.panMasked = data.pan_masked
  }
}
async function saveInvestor(): Promise<void> {
  const name = investorForm.name.trim()
  if (!name || panError.value) return
  const pan = investorForm.pan.trim().toUpperCase()
  // relation / is_huf aren't part of v1 — let the backend apply its defaults
  // rather than hardcoding a "Self" the UI can't actually edit.
  const fields = { name, family_id: investorForm.familyId }
  const ok =
    investorForm.mode === 'create'
      ? await investorStore.createInvestor({ ...fields, email: '', ...(pan ? { pan } : {}) })
      : await investorStore.updateInvestor(investorForm.id as number, {
          ...fields,
          ...(pan ? { pan } : {}),
        })
  if (ok) {
    investorForm.visible = false
    ui.notify({
      severity: 'success',
      summary: investorForm.mode === 'create' ? 'Investor added' : 'Investor updated',
    })
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
        <Button
          label="New family"
          icon="pi pi-sitemap"
          severity="secondary"
          outlined
          size="small"
          :disabled="readOnly"
          @click="openCreateFamily"
        />
        <Button
          label="Add manually"
          icon="pi pi-user-plus"
          severity="secondary"
          outlined
          size="small"
          :disabled="readOnly"
          @click="openCreateInvestor()"
        />
        <!-- Primary path: an import creates the investor from the CAS automatically. -->
        <Button label="Import" icon="pi pi-download" size="small" @click="goImport" />
      </div>
    </header>

    <section v-if="agg && !roster.isEmpty" class="roster-summary">
      <div class="rs-main">
        <p class="rs-label">Total net worth</p>
        <p class="rs-value">{{ formatInr(agg.totalInr) }}</p>
        <p class="rs-sub">
          {{ agg.investorCount }} investor{{ agg.investorCount === 1 ? '' : 's' }}
          <template v-if="agg.familyCount > 0">
            · {{ agg.familyCount }} {{ agg.familyCount === 1 ? 'family' : 'families' }}</template
          >
          <span
            v-if="agg.navsStale && agg.navsAsOf"
            class="rs-stale"
            title="Prices haven't refreshed recently — values use the last NAV on file"
          >
            · <i class="pi pi-exclamation-triangle" aria-hidden="true" /> prices as of
            {{ formatDate(agg.navsAsOf) }}
          </span>
        </p>
      </div>
      <div
        class="rs-integrity"
        :title="`${agg.taxReadyCount} of ${agg.integrityUnitCount} holdings tax-ready`"
      >
        <span class="dot ok" /> {{ agg.taxReadyCount }}/{{ agg.integrityUnitCount }} tax-ready
        <span v-if="agg.needsAttentionCount > 0" class="rs-attn">
          <span class="dot attention" /> {{ agg.needsAttentionCount }} need attention
        </span>
      </div>
    </section>

    <div v-if="roster.isEmpty" class="empty">
      <i class="pi pi-inbox" />
      <p>No investors yet.</p>
      <p class="muted">
        Import a CAS to get started — the investor is created automatically from the statement, with
        their full holdings.
      </p>
      <div v-if="!ui.isMobile" class="empty-actions">
        <Button label="Import" icon="pi pi-download" @click="goImport" />
        <Button
          label="Add manually"
          icon="pi pi-user-plus"
          severity="secondary"
          text
          :disabled="readOnly"
          @click="openCreateInvestor()"
        />
      </div>
    </div>

    <template v-else>
      <div class="toolbar">
        <IconField class="search">
          <InputIcon class="pi pi-search" />
          <InputText v-model="filterText" placeholder="Find an investor or family…" />
        </IconField>
        <ToggleButton
          v-if="hasFamilies"
          v-model="grouped"
          on-label="Grouped by family"
          off-label="Flat list"
          on-icon="pi pi-sitemap"
          off-icon="pi pi-list"
          size="small"
        />
      </div>

      <DataTable
        :value="rows"
        data-key="id"
        :filters="filters"
        :global-filter-fields="['name', 'familyName']"
        :row-group-mode="grouped ? 'subheader' : undefined"
        :group-rows-by="grouped ? 'familyName' : undefined"
        :sort-field="grouped ? 'familyName' : undefined"
        :sort-order="grouped ? 1 : undefined"
        removable-sort
        class="roster-table"
        @row-click="(e) => openInvestor((e.data as Row).id)"
      >
        <template #groupheader="{ data }">
          <div class="grp">
            <span class="grp-name">{{ (data as Row).familyName }}</span>
            <span class="count-tag">{{ memberCount((data as Row).familyName) }}</span>
            <template v-if="(data as Row).familyId != null">
              <span v-if="familyCombined((data as Row).familyName)" class="grp-combined">
                {{ familyCombined((data as Row).familyName) }}
                <span
                  class="dot"
                  :class="familyNeedsAttention((data as Row).familyName) ? 'attention' : 'ok'"
                />
              </span>
              <button
                type="button"
                class="grp-link is-link"
                @click.stop="openFamily((data as Row).familyId!)"
              >
                Open dashboard <i class="pi pi-angle-right" />
              </button>
              <Button
                icon="pi pi-ellipsis-v"
                text
                rounded
                size="small"
                class="grp-menu"
                aria-label="Family actions"
                @click.stop="openFamilyMenu($event, (data as Row).familyName)"
              />
            </template>
          </div>
        </template>

        <Column field="name" header="Investor" :sortable="!grouped">
          <template #body="{ data }">
            <div class="inv-name-cell">
              <button
                type="button"
                class="inv-name is-link"
                @click.stop="openInvestor((data as Row).id)"
              >
                {{ (data as Row).name }}
              </button>
              <span
                v-if="!(data as Row).hasPan"
                class="no-pan"
                title="No PAN on file — add one to enable the capital-gains export (optional)"
                >no PAN</span
              >
              <span v-if="!grouped && (data as Row).familyId != null" class="row-family">{{
                (data as Row).familyName
              }}</span>
            </div>
          </template>
        </Column>

        <Column
          field="valueNum"
          header="Value"
          :sortable="!grouped"
          class="col-num"
          header-class="col-num"
        >
          <template #body="{ data }">
            <span class="value" :class="{ pending: isPending((data as Row).summary) }">
              {{ valueText((data as Row).summary) }}
              <small
                v-if="!isPending((data as Row).summary) && unpriced((data as Row).summary) > 0"
                class="unpriced-flag"
                :title="`Total excludes ${unpriced((data as Row).summary)} fund(s) we couldn't price (no NAV yet).`"
                >⚠ {{ unpriced((data as Row).summary) }}</small
              >
            </span>
          </template>
        </Column>

        <Column header="Verified" class="col-num" header-class="col-num">
          <template #body="{ data }">
            <span class="tax-ready" :title="integrityTitle((data as Row).summary)">
              <template v-if="(data as Row).summary">
                <span class="dot" :class="integrityTone((data as Row).summary)" />
                {{ (data as Row).summary!.taxReadyCount }}/{{
                  (data as Row).summary!.integrityUnitCount
                }}
              </template>
              <template v-else>—</template>
            </span>
          </template>
        </Column>

        <Column header="Imported" class="col-num" header-class="col-num">
          <template #body="{ data }">
            <span class="muted">{{ formatDate((data as Row).summary?.lastImportAt) }}</span>
          </template>
        </Column>

        <Column class="col-actions">
          <template #body="{ data }">
            <Button
              icon="pi pi-ellipsis-v"
              text
              rounded
              size="small"
              aria-label="Investor actions"
              @click.stop="openInvestorMenu($event, data as Row)"
            />
          </template>
        </Column>
        <!-- Spacer column: in subheader row-group mode PrimeVue renders the group
             header cell with colspan = (columns − 1), so the family band stops one
             column short of the table edge. This hidden throwaway column absorbs
             that offset so the band spans the full visible width. Do not remove.
             See: https://github.com/primefaces/primevue/issues/3685#issuecomment-2107187144 -->
        <Column style="display: none" />
        <template #empty>
          <span class="muted">No investor matches “{{ filterText }}”.</span>
        </template>
      </DataTable>
    </template>

    <Menu ref="rowMenu" :model="rowMenuModel" popup />

    <!-- Family create / rename -->
    <Dialog
      v-model:visible="familyForm.visible"
      modal
      :header="familyForm.mode === 'create' ? 'New family' : 'Rename family'"
      :style="{ width: '24rem' }"
    >
      <div class="field">
        <label for="family-name">Family name</label>
        <InputText id="family-name" v-model="familyForm.name" autofocus @keyup.enter="saveFamily" />
      </div>
      <template #footer>
        <Button label="Cancel" severity="secondary" outlined @click="familyForm.visible = false" />
        <Button
          label="Save"
          :loading="familyStore.saving"
          :disabled="!familyForm.name.trim() || readOnly"
          @click="saveFamily"
        />
      </template>
    </Dialog>

    <!-- Investor create / edit (family reassignment lives here, not in the row) -->
    <Dialog
      v-model:visible="investorForm.visible"
      modal
      :header="investorForm.mode === 'create' ? 'New investor' : 'Edit investor'"
      :style="{ width: '26rem' }"
    >
      <div class="field">
        <label for="inv-name">Name</label>
        <InputText id="inv-name" v-model="investorForm.name" autofocus />
      </div>
      <div class="field">
        <label for="inv-pan"
          >PAN <span v-if="!investorForm.hasPan" class="optional">(optional)</span></label
        >
        <template v-if="investorForm.panLocked">
          <div class="pan-locked">
            <i class="pi pi-lock" />
            <span>{{ investorForm.panMasked || 'On file' }}</span>
          </div>
          <small class="field-hint">
            Statements are already imported under this PAN, so it's the key they attach to. Changing
            it would split this investor's holdings across two records.
          </small>
        </template>
        <template v-else>
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
          <small v-else-if="investorForm.hasPan" class="field-hint">
            Current: {{ investorForm.panMasked }} · leave blank to keep, or type a new PAN to
            replace it.
          </small>
          <small v-else class="field-hint">
            Needed for the per-PAN capital-gains (Schedule 112A) export.
          </small>
        </template>
      </div>
      <div class="field">
        <label for="inv-family">Family</label>
        <Select
          id="inv-family"
          v-model="investorForm.familyId"
          :options="familyOptions"
          option-label="label"
          option-value="value"
        />
      </div>
      <template #footer>
        <Button
          label="Cancel"
          severity="secondary"
          outlined
          @click="investorForm.visible = false"
        />
        <Button
          label="Save"
          :loading="investorStore.saving"
          :disabled="!investorForm.name.trim() || !!panError || readOnly"
          @click="saveInvestor"
        />
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

/* Roster summary strip */
.roster-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--fm-space-4);
  flex-wrap: wrap;
  padding: var(--fm-space-4) var(--fm-space-5);
  margin-bottom: var(--fm-space-5);
  background: var(--fm-surface);
  border: 1px solid var(--fm-border-subtle);
  border-radius: var(--fm-radius-lg);
  box-shadow: var(--fm-shadow-sm);
}
.rs-label {
  margin: 0;
  font-size: 0.6875rem;
  font-weight: 500;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--fm-text-muted);
}
.rs-value {
  margin: 0.1rem 0 0;
  font-size: 1.75rem;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.rs-sub {
  margin: 0.1rem 0 0;
  font-size: 0.8125rem;
  color: var(--fm-text-muted);
}
.rs-stale {
  color: var(--p-amber-600, #d97706);
  font-weight: 600;
}
.rs-stale .pi {
  font-size: 0.7rem;
}
.rs-integrity {
  display: inline-flex;
  align-items: center;
  gap: var(--fm-space-2);
  font-size: 0.8125rem;
  color: var(--fm-text-muted);
}
.rs-attn {
  display: inline-flex;
  align-items: center;
  gap: var(--fm-space-1);
  margin-left: var(--fm-space-3);
  color: var(--fm-critical);
  font-weight: 500;
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
.empty .muted {
  max-width: 28rem;
  margin-inline: auto;
  color: var(--fm-text-muted);
}
.empty-actions {
  display: inline-flex;
  align-items: center;
  gap: var(--fm-space-2);
}

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--fm-space-3);
  margin-bottom: var(--fm-space-3);
  flex-wrap: wrap;
}
.search {
  flex: 1;
  max-width: 22rem;
}
.search :deep(input) {
  width: 100%;
}

/* Group subheader */
.grp {
  display: flex;
  align-items: center;
  gap: var(--fm-space-3);
  width: 100%;
}
.grp-name {
  font-weight: 600;
}
.count-tag {
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--fm-text-muted);
  background: var(--fm-surface);
  border-radius: var(--fm-radius-pill);
  padding: 0.05rem 0.5rem;
}
.grp-combined {
  display: inline-flex;
  align-items: center;
  gap: var(--fm-space-2);
  color: var(--fm-text-muted);
  font-size: 0.8125rem;
  font-variant-numeric: tabular-nums;
}
.grp-link {
  margin-left: auto;
  font-size: 0.8125rem;
}
.grp-menu {
  margin-left: 0;
}

/* Cells */
.inv-name-cell {
  display: inline-flex;
  align-items: center;
  gap: var(--fm-space-2);
  min-width: 0;
}
.row-family {
  font-size: 0.6875rem;
  color: var(--fm-text-subtle);
  text-transform: uppercase;
  letter-spacing: 0.03em;
}
/* PAN is the norm, so we don't badge it. Flag only its absence — quietly, since a
   user may deliberately have no PAN (no warning colour, no nag). */
.no-pan {
  font-size: 0.625rem;
  letter-spacing: 0.02em;
  color: var(--fm-text-subtle);
  border: 1px dashed var(--fm-border);
  border-radius: var(--fm-radius-sm);
  padding: 0.02rem 0.3rem;
  cursor: help;
}
.value {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}
.value.pending {
  color: var(--fm-text-muted);
  font-weight: 500;
  font-size: 0.8125rem;
}
.unpriced-flag {
  margin-left: 0.35rem;
  font-size: 0.6875rem;
  font-weight: 600;
  color: var(--fm-warn);
  white-space: nowrap;
  cursor: help;
}
.tax-ready {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  color: var(--fm-text-muted);
  font-size: 0.8125rem;
}
:deep(.col-num) {
  text-align: right;
}
/* PrimeVue wraps the header label in a flex row, so text-align can't move it —
   right-align the header content to sit over the right-aligned cell data. */
:deep(th.col-num .p-datatable-column-header-content) {
  justify-content: flex-end;
}
:deep(.col-actions) {
  width: 3rem;
  text-align: right;
}
.roster-table :deep(.p-datatable-tbody > tr) {
  cursor: pointer;
}
/* Group-header is a full-width band. PrimeVue spans its cell over all columns but
   the last, so paint the row itself to fill that trailing column (no seam/gap). */
.roster-table :deep(.p-datatable-row-group-header),
.roster-table :deep(.p-datatable-row-group-header > td) {
  background: var(--fm-surface-raised);
  cursor: default;
}

/* Integrity dot */
.dot {
  display: inline-block;
  width: 0.5rem;
  height: 0.5rem;
  border-radius: 50%;
  flex: none;
  background: var(--fm-text-subtle);
}
.dot.ok {
  background: var(--fm-verified);
}
.dot.snapshot {
  background: var(--fm-warn);
}
.dot.attention {
  background: var(--fm-critical);
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
.field .optional {
  font-weight: 400;
  color: var(--fm-text-subtle, var(--fm-text-muted));
}
.pan-locked {
  display: inline-flex;
  align-items: center;
  gap: var(--fm-space-2);
  padding: 0.45rem 0.6rem;
  border: 1px solid var(--fm-border);
  border-radius: var(--fm-radius-sm);
  background: var(--fm-surface-raised);
  color: var(--fm-text-muted);
  font-size: 0.875rem;
  font-weight: 500;
}
.field-hint {
  font-size: 0.75rem;
  color: var(--fm-text-muted);
}
.field-error {
  font-size: 0.75rem;
  color: var(--fm-danger, #d32f2f);
}

@media (max-width: 640px) {
  .roster {
    padding: var(--fm-space-4);
  }
}
</style>
