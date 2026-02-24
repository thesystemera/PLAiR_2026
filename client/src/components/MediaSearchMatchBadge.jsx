import { memo, useState } from 'react'
import { useDynamicTheme } from '../contexts/DynamicThemeContext'

const CATEGORY_FALLBACK_COLORS = [
  '#8b5cf6', // purple
  '#ec4899', // pink
  '#06b6d4', // cyan
  '#10b981', // green
  '#f59e0b', // orange
]

function getCategoryColorIndex(category) {
  if (!category) return 0
  let hash = 0
  for (let i = 0; i < category.length; i++) {
    hash = ((hash << 5) - hash) + category.charCodeAt(i)
    hash = hash & hash
  }
  return Math.abs(hash) % CATEGORY_FALLBACK_COLORS.length
}

function getCategoryLabel(category) {
  if (!category) return 'General'
  return category.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())
}

export const MediaSearchMatchBadge = memo(function MediaSearchMatchBadge({
  similarityScore,
  intentCategory,
  matchWeights
}) {
  const {
    getCategoryMetadata,
    getBorder,
    getCardBackground,
    getWhite,
    getGrey300,
    getGrey400
  } = useDynamicTheme()

  const [showTooltip, setShowTooltip] = useState(false)

  if (!similarityScore || !matchWeights) {
    return null
  }

  const matchPercent = Math.round(similarityScore * 100)

  const primaryMetadata = getCategoryMetadata(intentCategory)
  const primaryMeta = {
    color: primaryMetadata?.color || CATEGORY_FALLBACK_COLORS[getCategoryColorIndex(intentCategory)],
    label: primaryMetadata?.label || getCategoryLabel(intentCategory),
    icon: primaryMetadata?.icon
  }

  const topCategories = Object.entries(matchWeights)
    .sort(([, a], [, b]) => b - a)
    .slice(0, 3)
    .filter(([, weight]) => weight > 0.01)

  return (
    <div
      className="relative mt-2 mb-1 flex items-center gap-2"
      onMouseEnter={() => setShowTooltip(true)}
      onMouseLeave={() => setShowTooltip(false)}
    >
      <div className="flex items-center gap-1.5 text-xs">
        <span
          className="font-bold tabular-nums"
          style={{ color: primaryMeta.color }}
        >
          {matchPercent}%
        </span>

        <div className="h-3 w-[1px]" style={{ backgroundColor: getBorder(0.3) }} />

        <div className="flex items-center gap-1">
          {topCategories.map(([category, weight]) => {
            const metadata = getCategoryMetadata(category)
            const color = metadata?.color || CATEGORY_FALLBACK_COLORS[getCategoryColorIndex(category)]
            const Icon = metadata?.icon
            const size = 12 + (weight * 2)

            if (!Icon) return null

            return (
              <Icon
                key={category}
                size={size}
                style={{ color }}
                className="flex-shrink-0 opacity-90"
              />
            )
          })}
        </div>
      </div>

      {showTooltip && (
        <div
          className="absolute bottom-full left-0 mb-2 w-64 rounded-lg shadow-xl p-3 z-50 border"
          style={{
            backgroundColor: getCardBackground(),
            borderColor: getBorder(0.2),
            backdropFilter: 'blur(20px)'
          }}
        >
          <div className="text-xs font-semibold mb-2" style={{ color: getWhite() }}>
            Match Breakdown
          </div>

          <div className="mb-2 pb-2 border-b" style={{ borderColor: getBorder(0.1) }}>
            <div className="flex items-center justify-between text-xs">
              <span style={{ color: getGrey400() }}>Overall:</span>
              <span className="font-semibold" style={{ color: getWhite() }}>{matchPercent}%</span>
            </div>
          </div>

          <div className="space-y-1.5">
            {Object.entries(matchWeights)
              .sort(([, a], [, b]) => b - a)
              .map(([category, weight]) => {
                const metadata = getCategoryMetadata(category)
                const color = metadata?.color || CATEGORY_FALLBACK_COLORS[getCategoryColorIndex(category)]
                const label = metadata?.label || getCategoryLabel(category)
                const Icon = metadata?.icon
                const percent = Math.round(weight * 100)

                if (percent < 1) return null

                return (
                  <div key={category} className="flex items-center gap-2">
                    {Icon && (
                      <Icon
                        size={12}
                        style={{ color }}
                        className="flex-shrink-0"
                      />
                    )}
                    <span className="text-[11px] w-12 flex-shrink-0" style={{ color: getGrey400() }}>
                      {label}:
                    </span>
                    <div className="flex-1 h-1.5 rounded-full overflow-hidden" style={{ backgroundColor: 'rgba(255,255,255,0.1)' }}>
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: `${percent}%`,
                          backgroundColor: color
                        }}
                      />
                    </div>
                    <span className="text-[11px] font-semibold w-8 text-right" style={{ color: getGrey300() }}>
                      {percent}%
                    </span>
                  </div>
                )
              })}
          </div>

          <div className="mt-2 pt-2 border-t text-[10px]" style={{ borderColor: getBorder(0.1), color: getGrey400() }}>
            Primary: <span style={{ color: primaryMeta.color }}>
              {primaryMeta.label}
            </span>
          </div>
        </div>
      )}
    </div>
  )
})