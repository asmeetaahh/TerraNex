import { Navigate, Route, Routes } from 'react-router-dom'
import { DashboardLayout } from './components/layout/DashboardLayout'
import { DashboardPage } from './pages/DashboardPage'
import { FarmHealthPage } from './pages/FarmHealthPage'
import { CropIntelligencePage } from './pages/CropIntelligencePage'
import { DiagnosisPage } from './pages/DiagnosisPage'
import { RecommendationsPage } from './pages/RecommendationsPage'
import { WeatherRisksPage } from './pages/WeatherRisksPage'
import { AiAdvisoryPage } from './pages/AiAdvisoryPage'
import { RegenerativePage } from './pages/RegenerativePage'
import { DataExplorerPage } from './pages/DataExplorerPage'
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
        <Route path="farms/recommendations" element={<RecommendationsPage />} />
        <Route path="farms/weather-risks" element={<WeatherRisksPage />} />
        <Route path="farms/ai-advisory" element={<AiAdvisoryPage />} />
        <Route path="farms/regenerative" element={<RegenerativePage />} />
        <Route path="farms/data-explorer" element={<DataExplorerPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}

export default App
