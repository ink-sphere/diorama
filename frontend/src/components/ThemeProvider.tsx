"use client";

import { ThemeProvider as NextThemeProvider } from "next-themes";

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  return (
    <NextThemeProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      // Transitions on a theme swap read as a lag, not a flourish: every surface
      // and rule would crossfade at a different rate.
      disableTransitionOnChange
    >
      {children}
    </NextThemeProvider>
  );
}
