import { useState } from 'react'

export default function App() {
  const [count, setCount] = useState(0)

  return (
    <main className="container">
      <h1>Synapse</h1>
      <p>Your website and web app starts here.</p>
      <button onClick={() => setCount((c) => c + 1)}>Clicked {count} times</button>
    </main>
  )
}
