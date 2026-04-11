// src/App.jsx
import React, { useEffect } from 'react'
import { LoadScript } from '@react-google-maps/api'
import { useForestStore } from './store/forestStore'
import { initForest } from './services/scoringService'
import { Dashboard } from './components/Dashboard'

const LIBRARIES = ['visualization']

function App() {
  const trees = useForestStore(s => s.trees)

  useEffect(() => {
    initForest()
  }, [])

  return (
    <LoadScript
      googleMapsApiKey={import.meta.env.VITE_GOOGLE_MAPS_API_KEY}
      libraries={LIBRARIES}
    >
      <Dashboard />
    </LoadScript>
  )
}

export default App
