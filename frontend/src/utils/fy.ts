/** Indian financial-year helpers (FY runs 1 Apr – 31 Mar). */

/** FY start calendar year for a date: Jan–Mar belong to the prior year's FY. */
function fyStartYear(d: Date): number {
  return d.getMonth() >= 3 ? d.getFullYear() : d.getFullYear() - 1
}

/** Start year → "2024-25" label (matches the API's `^\d{4}-\d{2}$`). */
export function fyLabel(startYear: number): string {
  return `${startYear}-${String((startYear + 1) % 100).padStart(2, '0')}`
}

/** The current Indian FY label. */
export function currentFy(now: Date = new Date()): string {
  return fyLabel(fyStartYear(now))
}

/**
 * FY labels from the current FY back to `from` (default 2018-19 — Schedule 112A
 * grandfathering starts at 31-Jan-2018), newest first.
 */
export function fyOptions(now: Date = new Date(), from = 2018): string[] {
  const labels: string[] = []
  for (let year = fyStartYear(now); year >= from; year--) labels.push(fyLabel(year))
  return labels
}
