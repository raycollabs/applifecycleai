'use client';

export default function ErrorState({ error, reset }: { error: Error; reset: () => void }) {
  // Sample: Render recoverable error boundary with retry button.
  return (
    <section data-shell-state="error" role="alert">
      <h2>Something went wrong</h2>
      <p>{error.message}</p>
      <button type="button" onClick={reset}>Retry</button>
    </section>
  );
}
