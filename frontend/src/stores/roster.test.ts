import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api/client', async (importActual) => {
  const actual = await importActual<typeof import('@/api/client')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn(), DELETE: vi.fn() } }
})

import { api } from '@/api/client'
import { useRosterStore } from './roster'
import { useFamilyStore } from './family'
import { useInvestorStore } from './investor'

const mockGet = vi.mocked(api.GET)

function backendOk() {
  mockGet.mockImplementation((path: string) => {
    if (path === '/api/families/') {
      return Promise.resolve({ data: [{ id: 1, name: 'Sharma Family' }] }) as never
    }
    return Promise.resolve({
      data: [
        { id: 10, name: 'Rajesh', family_id: 1 },
        { id: 20, name: 'Anil', family_id: null },
      ],
    }) as never
  })
}

describe('roster store', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    setActivePinia(createPinia())
  })

  it('groups investors by family with an Unaffiliated bucket last', async () => {
    backendOk()
    const roster = useRosterStore()
    await roster.load()

    expect(roster.usingSeed).toBe(false)
    expect(roster.groups).toHaveLength(2)
    expect(roster.groups[0].family?.name).toBe('Sharma Family')
    expect(roster.groups[0].investors.map((i) => i.id)).toEqual([10])
    expect(roster.groups[1].family).toBeNull() // Unaffiliated
    expect(roster.groups[1].investors.map((i) => i.id)).toEqual([20])
  })

  it('falls back to seed data when the backend is unreachable', async () => {
    mockGet.mockResolvedValue({ error: { detail: 'boom' } } as never)
    const roster = useRosterStore()
    await roster.load()

    expect(roster.usingSeed).toBe(true)
    expect(roster.investors.length).toBeGreaterThan(0)
    expect(roster.error).not.toBeNull()
  })

  it('ensureLoaded loads once, reload always refetches', async () => {
    backendOk()
    const roster = useRosterStore()
    await roster.ensureLoaded()
    await roster.ensureLoaded()
    expect(mockGet).toHaveBeenCalledTimes(2) // families + investors, once

    await roster.reload()
    expect(mockGet).toHaveBeenCalledTimes(4) // refetched
  })
})

describe('family / investor CRUD invalidates the roster cache', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    setActivePinia(createPinia())
    backendOk()
  })

  it('creating a family reloads the roster', async () => {
    const roster = useRosterStore()
    const reload = vi.spyOn(roster, 'reload')
    vi.mocked(api.POST).mockResolvedValue({ data: { id: 2, name: 'Iyer Family' } } as never)

    const family = useFamilyStore()
    const created = await family.createFamily('Iyer Family')

    expect(created).toEqual({ id: 2, name: 'Iyer Family' })
    expect(reload).toHaveBeenCalledOnce()
  })

  it('moving an investor between families reloads the roster', async () => {
    const roster = useRosterStore()
    const reload = vi.spyOn(roster, 'reload')
    vi.mocked(api.PATCH).mockResolvedValue({ data: { id: 10, family_id: 2 } } as never)

    const investor = useInvestorStore()
    await investor.setFamily(10, 2)

    expect(reload).toHaveBeenCalledOnce()
  })
})
