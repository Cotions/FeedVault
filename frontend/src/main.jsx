import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import './fonts.css'
import './index.css'
import App from './App.jsx'
import { applyTheme, getActiveTheme } from './lib/theme.js'

// Before the first render, so a non-default theme never flashes green.
applyTheme(getActiveTheme())

// A data router (App's <Routes> under one catch-all route) for useBlocker:
// a page with unsaved edits asks before the app leaves it (lib/unsaved.js).
const router = createBrowserRouter([{ path: '*', element: <App /> }])

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
)
