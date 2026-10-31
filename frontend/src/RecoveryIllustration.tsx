/** Decorative explanation of the review flow; never represents a live result. */
export default function RecoveryIllustration() {
  return <svg className="recovery-illustration" viewBox="0 0 300 108" fill="none" aria-hidden="true">
    <path className="flow-line" d="M77 54h40m66 0h40" stroke="var(--accent-line)" strokeWidth="2" strokeDasharray="4 5" />
    <g className="flow-source">
      <rect x="13" y="25" width="64" height="58" rx="10" fill="var(--surface)" stroke="var(--line)" />
      <path d="m30 45-6 6 6 6m28-12 6 6-6 6m-11-17-5 22" stroke="var(--muted)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M31 71h28" stroke="var(--line)" strokeWidth="3" strokeLinecap="round" />
    </g>
    <g className="flow-review">
      <rect x="117" y="13" width="66" height="82" rx="11" fill="var(--panel)" stroke="var(--accent-line)" />
      <path d="M133 33h23m-23 12h34m-34 12h19" stroke="var(--muted)" strokeWidth="2.5" strokeLinecap="round" />
      <circle cx="162" cy="69" r="10" fill="var(--accent-soft)" stroke="var(--teal)" strokeWidth="1.5" />
      <path d="m169 77 5 5" stroke="var(--teal)" strokeWidth="2.5" strokeLinecap="round" />
    </g>
    <g className="flow-approval">
      <rect x="223" y="25" width="64" height="58" rx="10" fill="var(--accent-soft)" stroke="var(--accent-line)" />
      <path d="m255 38 14 5v11c0 9-14 16-14 16s-14-7-14-16V43l14-5Z" stroke="var(--teal)" strokeWidth="2" strokeLinejoin="round" />
      <path d="m249 53 4 4 8-8" stroke="var(--teal)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </g>
  </svg>;
}
