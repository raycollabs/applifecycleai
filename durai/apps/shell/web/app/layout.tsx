import type { ReactNode } from 'react';
import './shell.css';

export default function Layout({ children }: { children: ReactNode }) {
  // Sample: Open the shell. Check the document language and main landmark.
  return <html lang="en"><body><main>{children}</main></body></html>;
}
