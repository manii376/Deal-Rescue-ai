import { useEffect, useMemo, useState } from 'react'
import { api } from '../../api/client'
import type { AIStatus, StrategyChoice, TimeMachineAssessRequest } from '../../api/types'
import { displayName, formatDateTime } from '../../lib/format'
import type { RecordResolver, ResolvedRecord } from '../../lib/inspector'
import { routeHref } from '../../lib/route'
import { useApi } from '../../lib/useApi'
import { SyntheticBadge } from '../../ui/Badge'
import { Button } from '../../ui/Button'
import { buttonClass } from '../../ui/buttonClass'
import { Icon } from '../../ui/Icon'
import { PageHeader } from '../../ui/PageHeader'
import { Section } from '../../ui/Section'
import { Loading, StatusNotice } from '../../ui/StatusNotice'
import { AssessmentPanel } from './AssessmentPanel'
import { ActuallyFollowed, KnownThen, MemoryControl } from './BranchPanels'
import { defaultBranch, isAfter } from './branchTime'
import { StrategyMatrix } from './StrategyMatrix'
import styles from './TimeMachine.module.css'
import { TimelinePanel } from './TimelinePanel'
import { assessmentKey, useAssessments } from './useAssessments'

const FRAMING = 'Exploratory comparison. Scenarios are not predictions; they show what the recorded evidence does and does not support.'

/**
 * Deal Time Machine (TM3, no AI). Data flow, per exploration:
 *  - NOW (as_of) is fixed when the page opens, so every request evaluates the same reference time;
 *  - timeline: GET …/timeline (once);
 *  - branch view: GET …/timeline/branch with include_memory=false, for "what actually followed" and the rules then;
 *  - strategies: GET …/time-machine/strategies, the single source of the evidence known then (its `sources` are
 *    exactly what the maps cite) and of the memory status. With memory on, this is the only recall per update.
 * Anchor and memory opt-in are tied to the branch point (and memory also to the anchor), so a date change can never
 * reuse them. useApi aborts superseded requests and never shows a response for an older key.
 */
export function TimeMachine({ customerId, dealId, ai, onResolver }: {
  customerId: string
  dealId: string
  ai: AIStatus | null
  onResolver: (resolver: RecordResolver | null) => void
}) {
  const [asOf] = useState(() => new Date().toISOString())
  const scope = `${customerId}/${dealId}`
  const customer = useApi(`customer:${customerId}`, (s) => api.customers.get(customerId, s))
  const deal = useApi(`deal:${scope}`, (s) => api.deals.get(customerId, dealId, s))
  const timeline = useApi(`tm-timeline:${scope}:${asOf}`, (s) => api.timeMachine.timeline(customerId, dealId, asOf, s))

  // Records of this deal (current values) so the inspector can expand event sources and record refs.
  const interactions = useApi(`interactions:${scope}`, (s) => api.interactions.list(customerId, { deal_id: dealId, limit: 200 }, s))
  const commitments = useApi(`commitments:${scope}`, (s) => api.commitments.list(customerId, { deal_id: dealId, limit: 200 }, s))
  const stakeholders = useApi(`stakeholders:${customerId}`, (s) => api.stakeholders.list(customerId, { limit: 200 }, s))
  const resolver = useMemo<RecordResolver>(() => {
    const index = new Map<string, ResolvedRecord>()
    if (deal.state.status === 'ok') index.set(`deal:${deal.state.data.id}`, { type: 'deal', data: deal.state.data })
    if (interactions.state.status === 'ok') interactions.state.data.items.forEach((i) => index.set(`interaction:${i.id}`, { type: 'interaction', data: i }))
    if (commitments.state.status === 'ok') commitments.state.data.items.forEach((c) => index.set(`commitment:${c.id}`, { type: 'commitment', data: c }))
    if (stakeholders.state.status === 'ok') stakeholders.state.data.items.forEach((s) => index.set(`stakeholder:${s.id}`, { type: 'stakeholder', data: s }))
    return (type, id) => (id ? index.get(`${type}:${id}`) : undefined)
  }, [deal.state, interactions.state, commitments.state, stakeholders.state])
  useEffect(() => {
    onResolver(resolver)
  }, [resolver, onResolver])
  useEffect(() => () => onResolver(null), [onResolver])

  // Selection state. Derived values keep everything consistent without effects.
  const [chosenBranch, setChosenBranch] = useState<string | null>(null)
  const [anchor, setAnchor] = useState<{ ref: string; branchAt: string } | null>(null)
  const [memoryScope, setMemoryScope] = useState<string | null>(null)
  const branchAt = chosenBranch ?? (timeline.state.status === 'ok' ? defaultBranch(timeline.state.data) : null)
  const anchorRef = anchor && branchAt && anchor.branchAt === branchAt ? anchor.ref : null
  const scopeKey = `${branchAt}|${anchorRef ?? ''}`
  const includeMemory = memoryScope === scopeKey

  const branch = useApi(branchAt ? `tm-branch:${scope}:${branchAt}:${asOf}` : null,
    (s) => api.timeMachine.branch(customerId, dealId, { branch_at: branchAt as string, as_of: asOf, include_memory: false }, s))
  // TM5: AI assessments, one request at a time, only on an explicit click, tied to this exact selection.
  const assessments = useAssessments(customerId, dealId)

  const catalogue = useApi(branchAt ? `tm-strategies:${scope}:${branchAt}:${asOf}:${anchorRef ?? ''}:${includeMemory}` : null,
    (s) => api.timeMachine.strategies(customerId, dealId,
      { branch_at: branchAt as string, as_of: asOf, anchor_ref: anchorRef, include_memory: includeMemory }, s))

  if (timeline.state.status === 'error') {
    const e = timeline.state.error
    return (
      <StatusNotice tone="error" title={e.status === 404 ? 'Deal not found for this customer' : 'The timeline could not be loaded'}
        action={e.status === 404 ? undefined : <Button size="sm" icon="refresh" onClick={timeline.reload}>Try again</Button>}>
        {e.message}
      </StatusNotice>
    )
  }
  if (timeline.state.status !== 'ok' || !branchAt) return <Loading label="Reconstructing the recorded timeline…" />
  const tl = timeline.state.data

  const synthetic = customer.state.status === 'ok' && customer.state.data.is_synthetic
  const customerName = customer.state.status === 'ok' ? displayName(customer.state.data.name, synthetic) : 'Customer'
  const dealTitle = deal.state.status === 'ok' ? displayName(deal.state.data.title, synthetic) : 'Deal'
  const followed = branch.state.status === 'ok' ? branch.state.data.followed : null
  const catalogueBusy = catalogue.state.status === 'loading'
  const anchorError = catalogue.state.status === 'error' && catalogue.state.error.code === 'invalid_anchor'

  const pickBranch = (iso: string) => {
    if (isAfter(iso, tl.as_of)) return // the panel already refuses this; never send an invalid branch
    assessments.clear() // results belong to the previous branch point
    setChosenBranch(iso)
  }
  const chooseAnchor = (ref: string | null) => {
    assessments.clear('address_requirement_early')
    setAnchor(ref ? { ref, branchAt } : null)
  }
  const setMemory = (on: boolean) => {
    assessments.clear() // every strategy's evidence pack changes with memory
    setMemoryScope(on ? scopeKey : null)
  }
  const bodyFor = (choice: StrategyChoice): TimeMachineAssessRequest => ({
    strategy_id: choice.template.id, branch_at: branchAt, as_of: asOf,
    anchor_ref: choice.template.needs_anchor ? anchorRef : null, include_memory: includeMemory,
  })
  const renderAssessment = (choice: StrategyChoice) => {
    const body = bodyFor(choice)
    const entry = assessments.current(choice.template.id, assessmentKey(customerId, dealId, body))
    const blocked = !choice.template.offered ? 'Not offered at this date, so it cannot be assessed.'
      : choice.template.needs_anchor && !anchorRef ? 'Choose and apply a requirement anchor first.'
        : catalogueBusy ? 'Waiting for the strategy data to load.'
          : assessments.busy && entry?.status !== 'loading' ? 'Another assessment is running; one at a time.'
            : null
    return (
      <AssessmentPanel entry={entry} blockedReason={blocked} includeMemory={includeMemory} ai={ai}
        onAssess={() => assessments.run(body)} onCancel={() => assessments.clear(choice.template.id)} />
    )
  }

  return (
    <article className={styles.page}>
      <PageHeader
        eyebrow={`Deal Time Machine · ${customerName}`}
        badges={synthetic ? <SyntheticBadge /> : null}
        title={dealTitle}
        description="Pick a point in this deal's recorded history to see what was known then, what the records show afterwards, and how the fixed alternative strategies line up against that evidence."
        actions={<a className={buttonClass('secondary', 'md')} href={routeHref({ customerId, dealId })}><Icon name="chevronLeft" /> Back to deal</a>}
      />

      <div className={styles.framing} role="note">
        <p><strong>{catalogue.state.status === 'ok' ? catalogue.state.data.disclaimer : FRAMING}</strong></p>
        <p>
          The timeline is reconstructed from the records that exist. It is not a change history of every deal field.
          No AI is used on this page.
        </p>
      </div>

      <Section id="tm-timeline" title="Recorded timeline"
        meta={`Now = ${formatDateTime(tl.as_of)} (fixed while this page is open).`}>
        {tl.limitations.length || tl.data_notes.length ? (
          <details className={styles.notes} open>
            <summary>Limitations of this reconstruction ({tl.limitations.length + tl.data_notes.length})</summary>
            <ul>
              {tl.limitations.map((l) => <li key={l}>{l}</li>)}
              {tl.data_notes.map((n) => <li key={n}>{n}</li>)}
            </ul>
          </details>
        ) : null}
        <TimelinePanel timeline={tl} branchAt={branchAt} onBranch={pickBranch} />
      </Section>

      <div className={styles.branchBar} role="status" aria-live="polite">
        <span className={styles.branchLabel}>Branch point</span>
        <span className={styles.branchValue}>{formatDateTime(branchAt)}</span>
        <span className={styles.muted}>→ now {formatDateTime(tl.as_of)}</span>
      </div>

      <div className={styles.split}>
        <Section id="tm-known" title={`Known on ${formatDateTime(branchAt)}`}
          meta="Recorded by the branch point. Rep notes are the sales rep's account, not the customer's words.">
          <MemoryControl enabled={includeMemory} busy={catalogueBusy}
            onChange={setMemory}
            status={catalogue.state.status === 'ok' ? catalogue.state.data.memory : null} />
          {catalogue.state.status === 'loading' ? <Loading label={includeMemory ? 'Loading evidence and recalling memory…' : 'Loading evidence known then…'} /> : null}
          {catalogue.state.status === 'error' ? (
            <StatusNotice tone="error" title={anchorError ? 'The selected anchor is not eligible at this date' : 'Evidence could not be loaded'}
              action={anchorError
                ? <Button size="sm" onClick={() => chooseAnchor(null)}>Clear anchor</Button>
                : <Button size="sm" icon="refresh" onClick={catalogue.reload}>Try again</Button>}>
              {catalogue.state.error.message}
            </StatusNotice>
          ) : null}
          {catalogue.state.status === 'ok' ? (
            <KnownThen sources={catalogue.state.data.sources} branchAt={branchAt}
              signals={branch.state.status === 'ok' ? branch.state.data.signals_then : null} />
          ) : null}
        </Section>

        <Section id="tm-followed" title="What the records show afterwards"
          meta={`Recorded events after the branch point, up to now. Not causes or effects: only what was recorded.`}>
          {branch.state.status === 'loading' ? <Loading label="Loading later events…" /> : null}
          {branch.state.status === 'error' ? (
            <StatusNotice tone="error" title="Later events could not be loaded"
              action={<Button size="sm" icon="refresh" onClick={branch.reload}>Try again</Button>}>
              {branch.state.error.message}
            </StatusNotice>
          ) : null}
          {followed ? <ActuallyFollowed events={followed} branchAt={branchAt} asOf={tl.as_of} /> : null}
        </Section>
      </div>

      <Section id="tm-strategies" title="Alternative strategies"
        meta="Three fixed scenarios, evaluated by deterministic rules at the branch point. Offered means the trigger applies to the recorded evidence, not that the strategy would have worked.">
        {catalogue.state.status === 'loading' ? <Loading label="Evaluating strategies at this date…" /> : null}
        {catalogue.state.status === 'error' ? (
          <p className={styles.muted}>Strategies are unavailable until the evidence above loads.</p>
        ) : null}
        {catalogue.state.status === 'ok' ? (
          <StrategyMatrix catalogue={catalogue.state.data} branchAt={branchAt} anchor={anchorRef} busy={catalogueBusy}
            onAnchor={chooseAnchor} renderAssessment={renderAssessment} />
        ) : null}
      </Section>
    </article>
  )
}
