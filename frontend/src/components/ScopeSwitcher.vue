<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import Select from 'primevue/select'
import { useRosterStore } from '@/stores/roster'
import { useUiStore } from '@/stores/ui'

interface SwitcherOption {
  key: string
  label: string
  kind: 'investor' | 'family'
  id: number
}

interface SwitcherGroup {
  label: string
  items: SwitcherOption[]
}

const router = useRouter()
const roster = useRosterStore()
const ui = useUiStore()

// One option group per family (a selectable "combined" entry first, then its
// investors), plus an "Unaffiliated" group of solo investors.
const groupedOptions = computed<SwitcherGroup[]>(() =>
  roster.groups.map((group) => {
    const items: SwitcherOption[] = []
    if (group.family) {
      items.push({
        key: `family:${group.family.id}`,
        label: `${group.family.name} — combined`,
        kind: 'family',
        id: group.family.id,
      })
    }
    for (const inv of group.investors) {
      items.push({ key: `investor:${inv.id}`, label: inv.name, kind: 'investor', id: inv.id })
    }
    return { label: group.family?.name ?? roster.UNAFFILIATED_LABEL, items }
  }),
)

const selectedKey = computed<string | null>(() => {
  if (ui.selectedInvestorId !== null) return `investor:${ui.selectedInvestorId}`
  if (ui.selectedFamilyId !== null) return `family:${ui.selectedFamilyId}`
  return null
})

function onSelect(key: string | null): void {
  if (!key) return
  const [kind, rawId] = key.split(':')
  const id = Number(rawId)
  if (kind === 'family') {
    ui.selectFamily(id)
    void router.push({ name: 'family', params: { familyId: id } })
  } else {
    ui.selectInvestor(id)
    void router.push({ name: 'dashboard', params: { investorId: id } })
  }
}
</script>

<template>
  <Select
    class="scope-switcher"
    :model-value="selectedKey"
    :options="groupedOptions"
    option-label="label"
    option-value="key"
    option-group-label="label"
    option-group-children="items"
    placeholder="Select investor or family"
    @update:model-value="onSelect"
  />
</template>

<style scoped>
/* Fill the sidebar column rather than forcing a fixed width wider than it (which
   spilled the box across the border into the main area). */
.scope-switcher {
  width: 100%;
  max-width: 100%;
}

/* Truncate long investor/family names instead of widening the control. */
.scope-switcher :deep(.p-select-label) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
