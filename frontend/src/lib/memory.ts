// One wording for memory evidence status, shared by the briefing and the Deal Time Machine.

import type { MemoryEvidenceStatus } from '../api/types'

/** A plain sentence for the status, with every non-zero exclusion count. Failure is never worded as "none found". */
export function memoryLine(m: MemoryEvidenceStatus): string {
  const excluded = [
    m.excluded_unlinked && `${m.excluded_unlinked} unlinked summary/ies`,
    m.excluded_stale && `${m.excluded_stale} stale`,
    m.excluded_other_deal && `${m.excluded_other_deal} from other deals`,
    m.excluded_deleted_sources && `${m.excluded_deleted_sources} of deleted records`,
    m.excluded_foreign_bank && `${m.excluded_foreign_bank} from another bank`,
    m.excluded_after_cutoff && `${m.excluded_after_cutoff} from after the selected date`,
  ].filter(Boolean)
  const head = {
    used: `Memory used: ${m.included} linked, current memor${m.included === 1 ? 'y' : 'ies'}.`,
    no_relevant_memories: 'Memory searched: no linked, current memories for this deal.',
    unavailable: `Memory unavailable${m.reason ? ` (${m.reason})` : ''}; database evidence was used.`,
    disabled: 'Memory is disabled; database evidence only.',
    not_requested: 'Memory was not requested; database evidence only.',
  }[m.status]
  return excluded.length ? `${head} Excluded: ${excluded.join(', ')}.` : head
}
