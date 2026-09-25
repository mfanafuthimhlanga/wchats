import { type OpenFinding, UNREPRODUCIBLE_FINDING, evidenceSentence, retestLine } from './opsFormat'

/**
 * The lines after a finding's description. The mono meta line carries the
 * vector and turn, one middle dot at most, with a non-breaking space so
 * "turn 4" never wraps apart. The evidence line under it says where the
 * evidence came from and which claims stood, in a sentence. The re-test
 * line under that says what the newest re-test found, and is absent when
 * the owner never ran one. It is a polite live region, so the change from
 * "Re-test running." to the outcome is announced. A finding no conversation
 * can reproduce says so on the last line, in place of a Re-test button. The
 * ops page's stylesheet styles these (.finding-meta, .finding-evidence,
 * .finding-retest, .finding-unreproducible), and
 * tests-unit/finding-meta-render.spec.ts renders this component against it.
 */
export default function FindingMeta({ finding }: { finding: OpenFinding }) {
  const retest = retestLine(finding.retest)
  return (
    <>
      <span className="mono finding-meta">
        {' '}
        {finding.attack_vector ?? 'unrecorded attack vector'}
        {finding.turn_count != null && <> · turn&nbsp;{finding.turn_count}</>}
      </span>
      <span className="finding-evidence">{evidenceSentence(finding)}</span>
      {retest && (
        <span className="finding-retest" aria-live="polite">
          {retest}
        </span>
      )}
      {!finding.retestable && <span className="finding-unreproducible">{UNREPRODUCIBLE_FINDING}</span>}
    </>
  )
}
