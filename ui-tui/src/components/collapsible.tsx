import { Box, Text } from '@hermes/ink'
import type { ReactNode } from 'react'

import type { Theme } from '../theme.js'

type SuffixSpacing = 'single' | 'double'

export function Chevron({
  baseColor,
  baseDim = false,
  bold = false,
  count,
  countColor,
  countDim = false,
  onToggle,
  open,
  suffix,
  suffixColor,
  suffixDim = false,
  suffixSpacing = 'single',
  t,
  title
}: ChevronProps) {
  const titleColor = baseColor ?? t.color.muted
  const countTextColor = countColor ?? titleColor
  const suffixTextColor = suffixColor ?? t.color.statusFg ?? t.color.muted

  return (
    <Box onClick={(e: any) => onToggle?.(!!e?.shiftKey || !!e?.ctrlKey)}>
      <Text color={t.color.accent}>{open ? '▾ ' : '▸ '}</Text>
      <Text bold={bold} color={titleColor} dim={baseDim}>
        {title}
      </Text>
      {typeof count === 'number' ? (
        <Text color={countTextColor} dim={countDim}>
          {' '}
          ({count})
        </Text>
      ) : null}
      {suffix ? (
        <Text color={suffixTextColor} dim={suffixDim}>
          {suffixSpacing === 'double' ? '  ' : ' '}
          {suffix}
        </Text>
      ) : null}
    </Box>
  )
}

interface ChevronProps {
  baseColor?: string
  baseDim?: boolean
  bold?: boolean
  count?: number
  countColor?: string
  countDim?: boolean
  onToggle?: (deep?: boolean) => void
  open: boolean
  suffix?: ReactNode
  suffixColor?: string
  suffixDim?: boolean
  suffixSpacing?: SuffixSpacing
  t: Theme
  title: ReactNode
}
