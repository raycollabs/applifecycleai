import './checkout.css';
import { ReactNode } from 'react';

export default function RootLayout({ children }: { children: ReactNode }) {
  // Sample: Wrap pages in HTML root layout with checkout stylesheet.
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
