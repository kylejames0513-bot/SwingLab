import { createContext, useContext, type ReactNode } from 'react';

import { colors, radii, space, type } from './tokens';

const theme = { colors, radii, space, type };

const ThemeContext = createContext(theme);

export function ThemeProvider({ children }: { children: ReactNode }) {
  return (
    <ThemeContext.Provider value={theme}>{children}</ThemeContext.Provider>
  );
}

export function useTheme() {
  return useContext(ThemeContext);
}
