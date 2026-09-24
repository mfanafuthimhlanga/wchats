import { type OpenFinding, evidenceSentence } from './opsFormat'

/**
 * The two lines after a finding's description. The mono meta line carries the
 * vector and turn, one middle dot at most. The evidence line under it says
 * where the evidence came from and which claims stood, in a sentence. The
 * ops page's stylesheet styles both (.finding-meta, .finding-evidence), and
 * tests-unit/finding-meta-render.spec.ts renders this component against it.
 */
export default function FindingMeta({ finding }: { finding: OpenFinding }) {
  return (
    <>
      <span className="mono finding-meta">
        {' '}
        {finding.attack_vector ?? 'unrecorded attack vector'}
        {finding.turn_count != null ? ` · turn ${finding.turn_count}` : ''}
      </span>
      <span className="finding-evidence">{evidenceSentence(finding)}</span>
    </>
  )
}
