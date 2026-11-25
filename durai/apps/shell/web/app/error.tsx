'use client';

export default function ErrorScreen({ reset }: { reset: () => void }) {
  // Sample: Fail a screen read. Check the error and select Retry.
  return <section data-shell-state="error">
    <h1>Cannot load this page</h1>
    <button type="button" onClick={reset}>Retry</button>
  </section>;
}
