import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App, { type AppProps } from './App'
import './index.css'
import './App.css'
import './Onboarding.css'
import './Welcome.css'
import './ProjectChooser.css'

async function appProps(): Promise<AppProps> {
  if (import.meta.env.DEV && import.meta.env.VITE_UI_ONLY === 'true') {
    const { createUiOnlyDependencies } = await import('./uiOnly')
    return createUiOnlyDependencies()
  }
  return {}
}

void appProps().then((props) => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App {...props} />
    </StrictMode>,
  )
})
