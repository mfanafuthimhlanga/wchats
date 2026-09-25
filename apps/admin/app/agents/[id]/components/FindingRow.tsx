import Btn from '../../../components/gotham/Btn'
import Chip from '../../../components/gotham/Chip'
import FindingMeta from './FindingMeta'
import { type OpenFinding, formatAttackVector, gateMessage, retestIsRunning } from './opsFormat'

/**
 * One open finding in the Adversary region: the critical banner (`banner`)
 * or a list row. Three flex items: the chip, the text column, and the
 * Re-test button. The text column holds the finding's sentence, its
 * FindingMeta lines and the API's refusal note, so the note starts where
 * the text starts, in the banner and the list alike.
 *
 * No hooks: AdversaryPanel owns the busy and note state, and
 * tests-unit/finding-meta-render.spec.ts renders this component itself.
 *
 * Null guards (23-09 adversarial review, finding 15): description,
 * attack_vector and turn_count are all nullable. The banner's sentence is
 * gateMessage(), the same locked fallback (OD-5) the gatebar uses. A list
 * row is not necessarily critical, so it falls back to a plain sentence.
 */
export default function FindingRow({
  finding,
  banner = false,
  busy,
  note,
  onRetest,
}: {
  finding: OpenFinding
  banner?: boolean
  busy: boolean
  note: string | undefined
  onRetest: (findingId: string) => void
}) {
  const working = busy || retestIsRunning(finding.retest)
  return (
    <div className={banner ? 'critical' : 'finding-row'} aria-busy={working || undefined}>
      {banner ? (
        <Chip verdict="seal">Critical</Chip>
      ) : (
        <Chip verdict={finding.severity === 'critical' ? 'seal' : 'mute'}>{finding.severity}</Chip>
      )}
      <div className="finding-body">
        <p className="finding-text">
          {banner ? gateMessage(finding) : finding.description || 'No description recorded.'}
          <FindingMeta finding={finding} />
        </p>
        {note && (
          <div className="help finding-note" role="status">
            {note}
          </div>
        )}
      </div>
      {finding.retestable && <RetestAction finding={finding} inactive={working} onRetest={onRetest} />}
    </div>
  )
}

// One click, no staged confirmation: a re-test changes nothing about the
// agent and at worst leaves the finding open with a fresh reading. While
// this finding's re-test runs or its request is in flight the button is
// aria-disabled, not disabled: a disabled button drops keyboard focus to
// <body>, and this one disables itself under the owner's focus. The click
// guard makes it inert. The aria-label starts with the visible label
// (WCAG 2.5.3) and names the vector the way the coverage ledger does.
function RetestAction({
  finding,
  inactive,
  onRetest,
}: {
  finding: OpenFinding
  inactive: boolean
  onRetest: (findingId: string) => void
}) {
  const vector = finding.attack_vector ? formatAttackVector(finding.attack_vector) : 'unrecorded attack vector'
  return (
    <Btn
      variant="ghost"
      className={inactive ? 'is-disabled' : undefined}
      aria-disabled={inactive || undefined}
      aria-label={`Re-test finding: ${vector}`}
      onClick={() => {
        if (!inactive) onRetest(finding.id)
      }}
    >
      Re-test
    </Btn>
  )
}
