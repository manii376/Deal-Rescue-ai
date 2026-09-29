import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
// Self-hosted fonts (docs/ui-ux-direction.md §4.3): bundled WOFF2, no runtime font requests.
import '@fontsource-variable/newsreader/opsz.css'
import '@fontsource/ibm-plex-sans/400.css'
import '@fontsource/ibm-plex-sans/500.css'
import '@fontsource/ibm-plex-sans/600.css'
import '@fontsource/ibm-plex-mono/400.css'
import '@fontsource/ibm-plex-mono/500.css'
import './styles/tokens.css'
import './styles/base.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
