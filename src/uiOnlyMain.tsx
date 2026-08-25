import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { createUiOnlyDependencies } from './uiOnly'
import './index.css'
import './App.css'
import './Onboarding.css'
import './Welcome.css'
import './ProjectChooser.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App {...createUiOnlyDependencies()} />
  </StrictMode>,
)
