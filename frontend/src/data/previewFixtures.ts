import type { components } from '../api/types.gen'

/**
 * Design-preview fixtures — every operation this dashboard renders is still
 * `501 NOT_IMPLEMENTED` in the running backend (see docs/API_CONTRACT.md
 * "Implementation status"). These objects are typed against the generated
 * schema so every field the UI reads is a real contract field, but the
 * VALUES are invented locally for visual development — nothing here is a
 * real observation. `DashboardPage` only renders them after a live call to
 * `GET /farms` returns `NOT_IMPLEMENTED`, and the page marks the result with
 * a persistent "preview" banner rather than presenting it as live data.
 */

type Farm = components['schemas']['Farm']
type FarmDashboard = components['schemas']['FarmDashboard']
type AnalysisRun = components['schemas']['AnalysisRun']
type AnalysisRunSummary = components['schemas']['AnalysisRunSummary']
type ScoredFactor = components['schemas']['ScoredFactor']
type DataSourceMeta = components['schemas']['DataSourceMeta']
type CropImage = components['schemas']['CropImage']
type VegetationSeries = components['schemas']['VegetationSeries']
type WeatherBundle = components['schemas']['WeatherBundle']

const now = new Date()
const iso = (daysAgo: number) => new Date(now.getTime() - daysAgo * 86_400_000).toISOString()

const simulatedMeta = (source: string, note?: string): DataSourceMeta => ({
  source,
  mode: 'simulated',
  fetched_at: iso(0),
  note: note ?? 'Generated locally; not a real observation.',
})

export const previewFarms: Farm[] = [
  {
    id: 'f1a2c3d4-0000-4a11-8b22-000000000001',
    name: 'North Ridge Farm',
    latitude: -1.2921,
    longitude: 36.8219,
    region: 'Kiambu',
    country_code: 'KE',
    area_hectares: 12.5,
    elevation_m: 1795,
    farming_practice: 'regenerative',
    irrigation_type: 'drip',
    notes: null,
    crop_count: 2,
    has_analysis: true,
    created_at: iso(140),
    updated_at: iso(2),
  },
  {
    id: 'f1a2c3d4-0000-4a11-8b22-000000000002',
    name: 'Riverside Plot',
    latitude: -1.3012,
    longitude: 36.7605,
    region: 'Kiambu',
    country_code: 'KE',
    area_hectares: 4.2,
    elevation_m: 1720,
    farming_practice: 'conventional',
    irrigation_type: 'rainfed',
    notes: null,
    crop_count: 1,
    has_analysis: false,
    created_at: iso(20),
    updated_at: iso(20),
  },
]

const factor = (
  key: string,
  label: string,
  score: number,
  weight: number,
  band: ScoredFactor['band'],
  explanation: string,
): ScoredFactor => ({ key, label, score, weight, band, explanation })

const analysis: AnalysisRun = {
  id: 'a9e8d7c6-1111-4a11-8b22-000000000001',
  farm_id: previewFarms[0].id,
  status: 'complete',
  created_at: iso(0.3),
  duration_ms: 4820,
  model: null,
  prompt_version: 'v1',
  ai_mode: 'mock',
  degraded_sources: [],
  overall_health_score: 78,
  overall_band: 'good',
  summary:
    'North Ridge is tracking well. Maize is in the grain-fill stage with strong NDVI, but the water balance has turned negative and irrigation within two days will prevent stress during a critical stage.',
  factors: [
    factor('crop_health', 'Crop Health', 82, 0.3, 'good', 'NDVI trend is stable and slightly above the maize benchmark for this growth stage.'),
    factor('water_balance', 'Water Balance', 61, 0.25, 'moderate', 'Seven-day precipitation fell short of crop demand by 18 mm.'),
    factor('soil_suitability', 'Soil Suitability', 84, 0.2, 'good', 'pH 6.2 and loam texture sit inside maize’s tolerated range.'),
    factor('disease_pressure', 'Disease Pressure', 88, 0.15, 'excellent', 'Humidity has stayed below the leaf-wetness threshold for blight all week.'),
    factor('weather_outlook', 'Weather Outlook', 74, 0.1, 'good', 'One heat-stress day forecast; no frost or high wind risk.'),
  ],
  weather_risk: {
    level: 'low',
    score: 74,
    forecast_window_days: 7,
    heat_stress_days: 1,
    frost_risk_days: 0,
    heavy_rain_days: 0,
    high_wind_days: 0,
    longest_dry_spell_days: 4,
    min_temp_c: 14.2,
    max_temp_c: 31.6,
    total_precipitation_mm: 6.4,
    drivers: ['One forecast day above 30 °C during grain fill', 'Four consecutive dry days mid-week'],
    explanation:
      'Conditions are mild overall. A single warm day midweek is unlikely to stress maize at this stage, and no frost or storm risk is forecast.',
    factors: [],
  },
  water_risk: {
    level: 'moderate',
    score: 61,
    water_balance_mm: -18.4,
    deficit_mm: 18.4,
    days_until_stress: 3,
    recommended_irrigation_mm: 22,
    total_crop_water_demand_mm: 41.2,
    total_precipitation_mm: 22.8,
    soil_moisture_pct: 34,
    water_holding_capacity_mm: 118,
    irrigation_window: 'within 48 hours',
    irrigation_efficiency_note: 'Drip irrigation on this farm applies at ~90% efficiency — 22 mm at the emitter should close the deficit.',
    drivers: ['Water balance −18.4 mm over the last 7 days', 'No rain forecast for the next 4 days'],
    explanation:
      'The water balance has turned negative during grain fill, the maize growth stage most sensitive to moisture stress. Without irrigation, stress is projected within 3 days.',
    factors: [],
  },
  disease_risk: {
    level: 'low',
    score: 88,
    conditions_summary: 'Low humidity and no sustained leaf wetness this week keep fungal pressure low.',
    explanation: 'No pathogen currently exceeds a low probability. Continue routine scouting during the next rain event.',
    risks: [
      {
        name: 'Northern corn leaf blight',
        pathogen: 'Exserohilum turcicum',
        crop_code: 'maize',
        level: 'low',
        probability: 0.12,
        triggering_conditions: ['Requires 6+ hours leaf wetness — not observed this week'],
        preventive_actions: ['Scout lower canopy after the next rain'],
        scouting_advice: 'Check lower leaves within 48 hours of any rainfall above 10 mm.',
      },
    ],
    factors: [],
  },
  crop_health: {
    score: 82,
    band: 'good',
    current_ndvi: 0.74,
    ndvi_trend: 'stable',
    growth_stage: 'grain_fill',
    days_since_planting: 68,
    days_to_expected_harvest: 34,
    gdd_accumulated: 1180,
    gdd_required: 1450,
    stress_indicators: [],
    explanation: 'NDVI has held steady for three weeks and sits above the benchmark for grain-fill stage maize on this soil type.',
    factors: [],
  },
  soil_assessment: {
    score: 84,
    band: 'good',
    ph_status: 'optimal',
    organic_matter_status: 'adequate',
    fertility_status: 'moderate',
    texture_class: 'clay_loam',
    limitations: [],
    explanation: 'Soil pH and texture both sit inside maize’s tolerated range, with adequate organic matter supporting nutrient retention.',
    factors: [
      factor('organic_carbon', 'Organic carbon', 70, 0.25, 'good', 'Organic carbon at 2.1%.'),
    ],
  },
  advisories: [
    {
      id: 'adv-0001',
      farm_id: previewFarms[0].id,
      analysis_run_id: 'a9e8d7c6-1111-4a11-8b22-000000000001',
      category: 'irrigation',
      priority: 'high',
      title: 'Irrigate within 48 hours',
      body: 'Apply 22 mm via the drip system to close the current water deficit before grain fill is affected. Split into two applications if the system runs below 8 L/h per emitter.',
      rationale: 'Water balance is −18.4 mm over the last 7 days and no rain is forecast for 4 days, with maize entering the moisture-sensitive grain-fill stage.',
      action_window: 'within 48 hours',
      confidence: 0.88,
      created_at: iso(0.3),
      dismissed_at: null,
    },
    {
      id: 'adv-0002',
      farm_id: previewFarms[0].id,
      analysis_run_id: 'a9e8d7c6-1111-4a11-8b22-000000000001',
      category: 'disease',
      priority: 'low',
      title: 'Scout after the next rain',
      body: 'Check the lower canopy for early blight lesions within 48 hours of any rainfall above 10 mm.',
      rationale: 'Leaf-wetness duration has stayed below the infection threshold, but the next rain event could change that quickly.',
      action_window: 'after next rainfall',
      confidence: 0.71,
      created_at: iso(0.3),
      dismissed_at: null,
    },
    {
      id: 'adv-0003',
      farm_id: previewFarms[0].id,
      analysis_run_id: 'a9e8d7c6-1111-4a11-8b22-000000000001',
      category: 'regenerative',
      priority: 'medium',
      title: 'Plan a cover crop after harvest',
      body: 'Sow a legume cover crop within a week of harvest to protect soil organic carbon through the dry window.',
      rationale: 'Organic matter is adequate but trending flat over the last three seasons; a cover crop compounds gains before the next planting.',
      action_window: 'post-harvest',
      confidence: 0.66,
      created_at: iso(0.3),
      dismissed_at: null,
    },
    {
      id: 'adv-0004',
      farm_id: previewFarms[0].id,
      analysis_run_id: 'a9e8d7c6-1111-4a11-8b22-000000000001',
      category: 'weather',
      priority: 'low',
      title: 'No action needed for the warm day midweek',
      body: 'The single forecast day above 30 °C is not expected to cause heat stress at the current growth stage.',
      rationale: 'Heat-stress days remain within the tolerated range for maize during grain fill.',
      action_window: null,
      confidence: 0.79,
      created_at: iso(0.3),
      dismissed_at: null,
    },
  ],
  crop_recommendations: [
    {
      rank: 1,
      crop_code: 'maize',
      crop_name: 'Maize',
      category: 'cereal',
      season: 'summer',
      is_current_crop: true,
      suitability_score: 84,
      water_requirement_mm: 550,
      planting_window: 'Mid-March to early April',
      expected_yield_note: '4.2–5.1 t/ha under current soil and irrigation conditions',
      rationale: 'Already the primary crop and well matched to this farm’s pH and texture.',
      strengths: ['Tolerates the farm’s pH 6.2', 'Drip irrigation covers peak demand'],
      considerations: ['Water balance needs active management during grain fill'],
      factors: [],
    },
    {
      rank: 2,
      crop_code: 'common_bean',
      crop_name: 'Common bean',
      category: 'legume',
      season: 'winter',
      is_current_crop: false,
      suitability_score: 77,
      water_requirement_mm: 350,
      planting_window: 'Early October',
      expected_yield_note: '1.4–1.8 t/ha as a short-rains rotation crop',
      rationale: 'A strong rotation partner — fixes nitrogen ahead of the next maize cycle and needs less water.',
      strengths: ['Lower water demand than maize', 'Improves soil nitrogen for the next season'],
      considerations: ['More sensitive to waterlogging on the lower plot'],
      factors: [],
    },
    {
      rank: 3,
      crop_code: 'sorghum',
      crop_name: 'Sorghum',
      category: 'cereal',
      season: 'summer',
      is_current_crop: false,
      suitability_score: 69,
      water_requirement_mm: 380,
      planting_window: 'Late March to mid-April',
      expected_yield_note: '2.8–3.4 t/ha, more drought-tolerant than maize',
      rationale: 'A resilient fallback if irrigation capacity becomes constrained.',
      strengths: ['High drought tolerance', 'Low input requirement'],
      considerations: ['Lower market price locally than maize'],
      factors: [],
    },
  ],
  regenerative_recommendations: [
    {
      rank: 1,
      practice_code: 'cover_cropping',
      practice_name: 'Cover cropping',
      relevance_score: 81,
      description: 'Sow a legume cover crop in the post-harvest window to protect and build soil organic carbon.',
      rationale: 'Organic matter is adequate but flat over three seasons; a legume cover crop compounds gains before erosion risk rises in the dry window.',
      expected_benefits: ['Raises soil organic carbon', 'Suppresses weed pressure ahead of next planting'],
      implementation_steps: ['Select a fast-establishing legume mix', 'Sow within a week of harvest', 'Terminate 2–3 weeks before next planting'],
      effort_level: 'moderate',
      time_to_benefit: '1-2 seasons',
      soil_carbon_impact: 'moderate increase over 3-5 seasons',
      water_retention_impact: 'improves infiltration on the clay-loam sections',
      considerations: [],
    },
    {
      rank: 2,
      practice_code: 'mulching',
      practice_name: 'Organic mulching',
      relevance_score: 74,
      description: 'Apply crop-residue mulch around the root zone to reduce evaporation during the dry spells between rain events.',
      rationale: 'The current dry-spell length (4 days) is pushing the water balance negative; mulch reduces evaporative loss between irrigations.',
      expected_benefits: ['Reduces irrigation frequency', 'Moderates soil temperature'],
      implementation_steps: ['Apply 5–7 cm of residue mulch after next irrigation'],
      effort_level: 'low',
      time_to_benefit: 'within this season',
      soil_carbon_impact: 'slight increase over time',
      water_retention_impact: 'reduces evaporative loss between irrigations',
      considerations: [],
    },
  ],
  sources: [
    simulatedMeta('open-meteo', 'Weather generated locally — no live provider connected yet.'),
    simulatedMeta('soilgrids', 'Soil profile generated locally — no live provider connected yet.'),
    simulatedMeta('sentinel-2-ndvi', 'Vegetation index generated locally — no live provider connected yet.'),
  ],
}

export const previewCropImage: CropImage = {
  id: 'img-0001',
  farm_id: previewFarms[0].id,
  farm_crop_id: 'fc-0001',
  content_type: 'image/jpeg',
  size_bytes: 2_140_000,
  width: 1200,
  height: 900,
  url: null,
  note: 'Lower leaves, east block',
  uploaded_at: iso(1.2),
  analysis_status: 'complete',
  analyzed_at: iso(1.1),
  ai_mode: 'mock',
  model: null,
  prompt_version: 'v1',
  analysis_error: null,
  analysis: {
    is_plant_material: true,
    crop_identified: 'maize',
    condition: 'gray_leaf_spot',
    condition_label: 'Gray leaf spot',
    severity: 'mild',
    confidence: 0.76,
    affected_area_pct: 6,
    symptoms_observed: ['Rectangular tan lesions on lower leaves', 'Lesions bounded by leaf veins'],
    differential_diagnoses: [
      { condition: 'northern_corn_leaf_blight', condition_label: 'Northern corn leaf blight', likelihood: 0.18, distinguishing_features: 'Blight lesions are larger and cigar-shaped rather than rectangular.' },
    ],
    immediate_actions: ['Improve airflow by checking plant spacing in this block', 'Avoid overhead irrigation in the affected rows'],
    treatment_options: [
      {
        name: 'Strobilurin fungicide',
        approach: 'chemical',
        description: 'Apply if lesions spread to the ear leaf.',
        timing: 'At first sign of spread, repeat in 14 days',
        precautions: 'Rotate fungicide class to avoid resistance buildup.',
      },
    ],
    prevention: ['Rotate away from maize in this block next season', 'Clear residue before the next planting'],
    disclaimer: 'AI-assisted diagnosis, not a substitute for an agronomist. Confirm before applying treatment.',
  },
}

export const previewDashboards: Record<string, FarmDashboard> = {
  [previewFarms[0].id]: {
    farm: previewFarms[0],
    has_analysis: true,
    analysis,
    current_weather: {
      observed_at: iso(0),
      temperature_c: 22.4,
      feels_like_c: 22.9,
      condition: 'partly_cloudy',
      condition_label: 'Partly cloudy',
      humidity_pct: 58,
      wind_speed_kmh: 11,
      wind_direction_deg: 140,
      precipitation_mm: 0,
      cloud_cover_pct: 45,
      pressure_hpa: 1013,
    },
    crops: [
      {
        id: 'fc-0001',
        farm_id: previewFarms[0].id,
        crop_id: 'c-maize',
        crop: {
          id: 'c-maize',
          code: 'maize',
          name: 'Maize',
          category: 'cereal',
          season: 'summer',
          drought_tolerance: 'moderate',
          optimal_temp_min_c: 18,
          optimal_temp_max_c: 30,
          ph_min: 5.8,
          ph_max: 7.2,
          base_temp_c: 10,
          gdd_to_maturity: 1450,
          water_need_mm_season: 550,
          common_diseases: ['northern_corn_leaf_blight', 'gray_leaf_spot'],
        },
        is_primary: true,
        status: 'growing',
        growth_stage: 'fruiting',
        planting_date: iso(68).slice(0, 10),
        expected_harvest_date: iso(-34).slice(0, 10),
        area_hectares: 9,
        notes: null,
        created_at: iso(68),
        updated_at: iso(0.3),
      },
    ],
    recent_images: [previewCropImage],
    data_freshness: analysis.sources ?? [],
  },
  [previewFarms[1].id]: {
    farm: previewFarms[1],
    has_analysis: false,
    analysis: null,
    current_weather: null,
    crops: [],
    recent_images: [],
    data_freshness: [],
  },
}

export const previewHealthHistory: AnalysisRunSummary[] = [
  62, 64, 63, 66, 68, 70, 69, 72, 74, 73, 76, 78,
].map((score, index, arr) => ({
  id: `run-${index}`,
  farm_id: previewFarms[0].id,
  status: 'complete',
  overall_health_score: score,
  overall_band: score >= 80 ? 'excellent' : score >= 65 ? 'good' : score >= 50 ? 'moderate' : 'poor',
  ai_mode: 'mock',
  duration_ms: 4200 + index * 30,
  degraded_sources: [],
  created_at: iso((arr.length - 1 - index) * 7),
}))

/** Consistent with `previewDashboards[farm].analysis.crop_health` (current_ndvi 0.74, trend "stable"). */
export const previewVegetation: Record<string, VegetationSeries> = {
  [previewFarms[0].id]: {
    farm_id: previewFarms[0].id,
    meta: simulatedMeta('sentinel-2-ndvi', 'Vegetation index generated locally — no live provider connected yet.'),
    current_ndvi: 0.74,
    mean_ndvi: 0.69,
    trend: 'stable',
    trend_pct: 3.2,
    series: [0.58, 0.61, 0.64, 0.67, 0.69, 0.71, 0.72, 0.73, 0.72, 0.74, 0.73, 0.74].map((ndvi, index, arr) => ({
      date: iso((arr.length - 1 - index) * 7).slice(0, 10),
      ndvi,
      evi: Math.round((ndvi * 0.82 + 0.02) * 1000) / 1000,
      cloud_cover_pct: 10 + ((index * 7) % 25),
    })),
  },
}

/**
 * Consistent with `previewDashboards[farm].analysis.weather_risk` and
 * `.current_weather`: min_temp_c 14.2 / max_temp_c 31.6 / total_precipitation_mm
 * 6.4 / longest_dry_spell_days 4 / heat_stress_days 1 all line up with the days below.
 */
export const previewWeatherBundle: Record<string, WeatherBundle> = {
  [previewFarms[0].id]: {
    farm_id: previewFarms[0].id,
    latitude: previewFarms[0].latitude,
    longitude: previewFarms[0].longitude,
    timezone: 'Africa/Nairobi',
    meta: simulatedMeta('open-meteo', 'Weather generated locally — no live provider connected yet.'),
    current: {
      observed_at: iso(0),
      temperature_c: 22.4,
      feels_like_c: 22.9,
      condition: 'partly_cloudy',
      condition_label: 'Partly cloudy',
      humidity_pct: 58,
      wind_speed_kmh: 11,
      wind_direction_deg: 140,
      precipitation_mm: 0,
      cloud_cover_pct: 45,
      pressure_hpa: 1013,
    },
    daily: [
      { date: iso(0).slice(0, 10), temp_min_c: 16, temp_max_c: 27, temp_mean_c: 21.5, humidity_mean_pct: 55, precipitation_mm: 0, precipitation_hours: 0, wind_max_kmh: 10, et0_mm: 4.2, condition: 'partly_cloudy' },
      { date: iso(-1).slice(0, 10), temp_min_c: 15, temp_max_c: 26, temp_mean_c: 20.5, humidity_mean_pct: 60, precipitation_mm: 0, precipitation_hours: 0, wind_max_kmh: 9, et0_mm: 4.0, condition: 'clear' },
      { date: iso(-2).slice(0, 10), temp_min_c: 14.2, temp_max_c: 25, temp_mean_c: 19.6, humidity_mean_pct: 62, precipitation_mm: 0, precipitation_hours: 0, wind_max_kmh: 8, et0_mm: 3.8, condition: 'clear' },
      { date: iso(-3).slice(0, 10), temp_min_c: 16, temp_max_c: 31.6, temp_mean_c: 23.8, humidity_mean_pct: 40, precipitation_mm: 0, precipitation_hours: 0, wind_max_kmh: 14, et0_mm: 5.1, condition: 'clear' },
      { date: iso(-4).slice(0, 10), temp_min_c: 17, temp_max_c: 28, temp_mean_c: 22.5, humidity_mean_pct: 58, precipitation_mm: 2.4, precipitation_hours: 2, wind_max_kmh: 12, et0_mm: 4.3, condition: 'light_rain' },
      { date: iso(-5).slice(0, 10), temp_min_c: 16, temp_max_c: 26, temp_mean_c: 21, humidity_mean_pct: 65, precipitation_mm: 4.0, precipitation_hours: 3, wind_max_kmh: 15, et0_mm: 3.9, condition: 'rain' },
      { date: iso(-6).slice(0, 10), temp_min_c: 15, temp_max_c: 27, temp_mean_c: 21, humidity_mean_pct: 55, precipitation_mm: 0, precipitation_hours: 0, wind_max_kmh: 10, et0_mm: 4.1, condition: 'partly_cloudy' },
    ],
  },
}
