/**
 * Regression for #20665: every collapsible header (Accordion, ToolTrail sections,
 * long system messages, agents overlay) renders through the one shared Chevron,
 * so the toggle glyph, count, and suffix read the same in each variant.
 */
import { PassThrough } from 'stream'

import { renderSync, Text } from '@hermes/ink'
import { stripAnsi } from '@hermes/shared/ansi'
import { type ReactElement } from 'react'
import { describe, expect, it } from 'vitest'

import { Accordion, Chevron, type ChevronTone } from '../components/accordion.js'
import { DEFAULT_THEME } from '../theme.js'

const renderText = (node: ReactElement) => {
  const stdout = new PassThrough()
  const stdin = new PassThrough()
  const stderr = new PassThrough()
  let output = ''

  Object.assign(stdout, { columns: 80, isTTY: false, rows: 10 })
  Object.assign(stdin, { isTTY: false })
  Object.assign(stderr, { isTTY: false })
  stdout.on('data', chunk => {
    output += chunk.toString()
  })

  const instance = renderSync(node, {
    patchConsole: false,
    stderr: stderr as NodeJS.WriteStream,
    stdin: stdin as NodeJS.ReadStream,
    stdout: stdout as NodeJS.WriteStream
  })

  instance.unmount()
  instance.cleanup()

  return stripAnsi(output)
}

describe('shared Chevron header', () => {
  const variants: { bold?: boolean; tone?: ChevronTone }[] = [
    {},
    { tone: 'error' },
    { tone: 'muted' },
    { tone: 'label' },
    { bold: true, tone: 'muted' }
  ]

  it.each(variants)('renders glyph, title, count and suffix for %o', variant => {
    for (const open of [false, true]) {
      const text = renderText(
        <Chevron {...variant} count={3} onClick={() => {}} open={open} suffix="extra" t={DEFAULT_THEME} title="Tools" />
      )

      expect(text).toContain(`${open ? '▾' : '▸'} Tools (3)`)
      expect(text).not.toContain(open ? '▸' : '▾')
      expect(text).toContain('extra')
    }
  })

  it('Accordion shows its body only when open and heads it with the same Chevron row', () => {
    const header = renderText(
      <Chevron bold count={2} onClick={() => {}} open suffix="connected" t={DEFAULT_THEME} title="MCP" tone="muted" />
    )

    const row = '▾ MCP (2) connected'

    const open = renderText(
      <Accordion count={2} open suffix="connected" t={DEFAULT_THEME} title="MCP">
        <Text>body-row</Text>
      </Accordion>
    )

    const closed = renderText(
      <Accordion count={2} open={false} suffix="connected" t={DEFAULT_THEME} title="MCP">
        <Text>body-row</Text>
      </Accordion>
    )

    expect(header).toContain(row)
    expect(open).toContain(row)
    expect(open).toContain('body-row')
    expect(closed).not.toContain('body-row')
  })
})
