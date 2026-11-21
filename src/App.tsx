const APP_URL = 'https://synapse-stock-mover.streamlit.app'
const REPO_URL = 'https://github.com/lorenjoseph-create/SYNAPSE'

const features = [
  { tone: 'teal', title: 'Plan', text: 'Warehouse sends, store-to-store moves and pull-backs, each with a one-line reason.' },
  { tone: 'gold', title: 'Explain', text: 'Data problems in plain language, with a chart and what was done about each one.' },
  { tone: 'coral', title: 'Approve', text: 'Tick the trips to approve, download the list, and rate the plan to tune future runs.' },
]

const stats = [
  { value: '1,166', label: 'moves planned' },
  { value: '11/11', label: 'rules passed' },
  { value: '26', label: 'data issues found' },
]

function Logo() {
  return (
    <svg width="32" height="32" viewBox="0 0 96 96" aria-hidden="true">
      <rect width="96" height="96" rx="22" fill="var(--deep)" />
      <path d="M28 64 C 40 64, 40 32, 52 32 S 64 52, 70 40" fill="none" stroke="var(--teal)" strokeWidth="6" strokeLinecap="round" />
      <circle cx="28" cy="64" r="9" fill="var(--gold)" />
      <circle cx="52" cy="32" r="7" fill="var(--sand)" />
      <circle cx="70" cy="40" r="9" fill="var(--coral)" />
    </svg>
  )
}

export default function App() {
  return (
    <>
      <header className="nav">
        <a className="brand" href="/">
          <Logo />
          <span>Synapse</span>
        </a>
        <a className="link" href={REPO_URL}>GitHub</a>
      </header>

      <main>
        <section className="hero">
          <p className="eyebrow">Stock Mover</p>
          <h1>Stock in the right store, every morning.</h1>
          <p className="lede">
            Synapse reads sales, stock and delivery data and writes a ready-to-approve list of stock moves. Every move
            has a reason, and every business rule is checked on the final plan.
          </p>
          <div className="actions">
            <a className="btn primary" href={APP_URL}>Open the live app</a>
            <a className="btn" href={REPO_URL}>View the code</a>
          </div>
          <div className="bar" aria-hidden="true">
            <span className="teal" /><span className="gold" /><span className="sand" /><span className="coral" />
          </div>
        </section>

        <section className="grid">
          {features.map((f) => (
            <article key={f.title} className={`card ${f.tone}`}>
              <h2>{f.title}</h2>
              <p>{f.text}</p>
            </article>
          ))}
        </section>

        <section className="stats" aria-label="Sample run, 1 November 2026">
          {stats.map((s) => (
            <div key={s.label}>
              <strong>{s.value}</strong>
              <span>{s.label}</span>
            </div>
          ))}
        </section>
      </main>

      <footer>Built for the Manyavar AI Hackathon.</footer>
    </>
  )
}
