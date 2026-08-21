import { Navigate, Route, Routes } from 'react-router-dom'
import { DashboardLayout } from './components/layout/DashboardLayout'
import { DashboardPage } from './pages/DashboardPage'
import { FarmHealthPage } from './pages/FarmHealthPage'
import { CropIntelligencePage } from './pages/CropIntelligencePage'
import { DiagnosisPage } from './pages/DiagnosisPage'
import { NotFoundPage } from './pages/NotFoundPage'

function App() {
  return (
    <Routes>
      <Route element={<DashboardLayout />}>
        <Route index element={<Navigate to="/farms" replace />} />
        <Route path="farms" element={<DashboardPage />} />
        <Route path="farms/farm-health" element={<FarmHealthPage />} />
        <Route path="farms/crop-intelligence" element={<CropIntelligencePage />} />
        <Route path="farms/diagnosis" element={<DiagnosisPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}

export default App
