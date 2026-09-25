import { type OpenFinding, evidenceSentence, retestLine } from './opsFormat'

/**
 * The lines after a finding's description. The mono meta line carries the
 * vector and turn, one middle dot at most. The evidence line under it says
 * where the evidence came from and which claims stood, in a sentence. The
 * re-test line under that says what the newest re-test found, and is absent
 * when the owner never ran one. It is a polite live region, so the change
 * from "Re-test running." to the outcome is announced. The ops page's
 * stylesheet styles all three (.finding-meta, .finding-evidence,
 * .finding-retest), and tests-unit/finding-meta-render.spec.ts renders this
 * component against it.
 */
export default function FindingMeta({ finding }: { finding: OpenFinding }) {
  const retest = retestLine(finding.retest)
  return (
    <>
      <span className="mono finding-meta">
        {' '}
        {finding.attack_vector ?? 'unrecorded attack vector'}
        {finding.turn_count != null ? ` · turn ${finding.turn_count}` : ''}
      </span>
      <span className="finding-evidence">{evidenceSentence(finding)}</span>
      {retest && (
        <span className="finding-retest" aria-live="polite">
          {retest}
        </span>
      )}
    </>
  )
}
