import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
// Bundled, not fetched. `index.html` used to pull Inter from rsms.me on every page load:
// a third party able to see when this instance is opened, and able to serve it whatever
// CSS it liked -- on the screen where the admin password is typed. It is also what stopped
// the interface looking right on an air-gapped install.
import '@fontsource-variable/inter'
import './index.css'
import App from './App.tsx'
import { I18nProvider } from './i18n/index.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <I18nProvider>
      <App />
    </I18nProvider>
  </StrictMode>,
)
