import { test, expect } from '@playwright/test'

const book = {
  id: 'characters-book',
  metadata: { title: 'The Lantern House', authors: ['Diorama test author'] },
  structure: ['The garden gate', 'An unexpected visitor'].map((title) => ({
    structure_type: 'chapter',
    structure_title: title,
    is_part_of_narrative: true,
    content: [
      {
        raw_text:
          `<h1>${title}</h1>` +
          Array.from(
            { length: 60 },
            (_, i) =>
              `<p>Paragraph ${i}. Mara opened the shutters. Someone was waiting beneath the pear tree.</p>`,
          ).join(''),
        markdown_text: 'Mara opened the shutters. Someone was waiting beneath the pear tree.',
      },
    ],
  })),
}
const profiles = [
  {
    id: 'mara',
    book_id: book.id,
    character_name: 'Mara Vale',
    character_alias: ['The Keeper'],
    character_profile_description:
      'Mara tends the lantern house on the edge of the village. Her quiet routine is interrupted by the arrival of a visitor carrying a letter addressed to her mother.\n\nShe is observant and fiercely protective of her younger brother. As the story unfolds, she begins to question the family history she has always accepted.',
    headshot_visual_prompt:
      'Dark, loosely tied hair and a small scar above the left eyebrow. Her age and eye colour are not specified.',
  },
  {
    id: 'alistair',
    book_id: book.id,
    character_name: 'Alistair Finch',
    character_alias: ['The visitor'],
    character_profile_description:
      'Alistair arrives at the lantern house with a letter. His connection to the Vale family is initially unclear.',
    headshot_visual_prompt: 'An older man with silver hair. Other facial features are unspecified.',
  },
]

test.beforeEach(async ({ page }) => {
  await page.route('**/api/runs', (route) => route.fulfill({ json: { runs: [], problems: [] } }))
  await page.route('**/api/runs/cast-book/storybook', (route) => route.fulfill({ json: book }))
  await page.route('**/api/runs/cast-book/characters', (route) => route.fulfill({ json: profiles }))
})

test('searches aliases, opens profiles, and restores reading scroll and preferences', async ({
  page,
}) => {
  let requests = 0
  page.on('request', (request) => {
    if (request.url().endsWith('/characters')) requests += 1
  })
  await page.goto('/?run=cast-book')
  await page.getByRole('button', { name: 'An unexpected visitor', exact: true }).click()
  const pane = page.locator('.reading-pane')
  await pane.evaluate((element) => {
    element.scrollTop = (element.scrollHeight - element.clientHeight) * 0.6
  })
  await page.getByRole('button', { name: 'Characters', exact: true }).click()
  await expect(page.getByRole('button', { name: 'View Mara Vale' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Mara Vale' })).toHaveCount(0)
  await expect(page.getByLabel('Character list').getByRole('button')).toHaveText([
    'Alistair FinchThe visitor',
    'Mara ValeThe Keeper',
  ])
  await page.getByLabel('Search characters').fill('KEEPER')
  await expect(page.getByRole('button', { name: 'View Alistair Finch' })).toHaveCount(0)
  await page.getByRole('button', { name: 'View Mara Vale' }).click()
  await expect(page.getByRole('heading', { name: 'Mara Vale' })).toBeVisible()
  await expect(page.getByLabel('In the story')).toContainText('younger brother')
  await page.getByText('Appearance', { exact: true }).click()
  await expect(page.getByText(/Dark, loosely tied hair/)).toBeVisible()
  await page.getByLabel('Reading settings').click()
  await page.getByRole('button', { name: 'Night', exact: true }).click()
  await page.getByRole('button', { name: 'Work Sans', exact: true }).click()
  await expect(page.locator('.character-description p').first()).toHaveCSS(
    'font-family',
    /Work Sans/,
  )
  await expect(page.locator('.reader')).toHaveClass(/theme-night/)
  await page.getByLabel('Reading settings').click()
  await page.getByRole('button', { name: 'Read', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'An unexpected visitor' })).toBeVisible()
  await expect(page).toHaveURL(/section=1$/)
  await expect
    .poll(() =>
      pane.evaluate((element) => element.scrollTop / (element.scrollHeight - element.clientHeight)),
    )
    .toBeCloseTo(0.6, 1)
  await page.getByRole('button', { name: 'Characters', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Mara Vale' })).toBeVisible()
  expect(requests).toBe(1)
  await page.getByRole('button', { name: 'Back to library' }).click()
  const saved = await page.evaluate(() =>
    JSON.parse(localStorage.getItem('diorama:v1:position:characters-book:cast-book')!),
  )
  expect(saved.path).toBe('1')
  expect(saved.ratio).toBeCloseTo(0.6, 1)
})

test('mobile cast list and profile navigation fit the viewport and support keyboard focus', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/?run=cast-book')
  await page.getByRole('button', { name: 'Characters', exact: true }).click()
  await page.getByLabel('Search characters').fill('unknown')
  await expect(page.getByText('No characters match “unknown”.')).toBeVisible()
  await page.getByRole('button', { name: 'Clear search', exact: true }).click()
  const mara = page.getByRole('button', { name: 'View Mara Vale' })
  await mara.focus()
  await mara.press('Enter')
  await expect(page.getByRole('heading', { name: 'Mara Vale' })).toBeFocused()
  await expect(page.getByLabel('Character list')).not.toBeVisible()
  await page.getByRole('button', { name: 'All characters' }).click()
  await expect(mara).toBeFocused()
  await expect(page.getByLabel('Character list')).toBeVisible()
  const width = await page.evaluate(() => ({
    body: document.documentElement.scrollWidth,
    viewport: innerWidth,
  }))
  expect(width.body).toBeLessThanOrEqual(width.viewport)
})

test('handles missing, malformed, and empty profiles with reload while reading stays available', async ({
  page,
}) => {
  await page.route('**/api/runs/cast-book/characters', (route) => route.fulfill({ status: 404 }))
  await page.goto('/?run=cast-book')
  await page.getByRole('button', { name: 'Characters', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'No character profiles yet' })).toBeVisible()
  await page.route('**/api/runs/cast-book/characters', (route) =>
    route.fulfill({ contentType: 'application/json', body: '{' }),
  )
  await page.getByRole('button', { name: 'Reload profiles' }).click()
  await expect(page.getByRole('alert')).toContainText('not valid JSON')
  await page.route('**/api/runs/cast-book/characters', (route) => route.fulfill({ json: [] }))
  await page.getByRole('button', { name: 'Reload profiles' }).click()
  await expect(page.getByRole('heading', { name: 'No characters found' })).toBeVisible()
  await page.route('**/api/runs/cast-book/characters', (route) => route.fulfill({ json: profiles }))
  await page.getByRole('button', { name: 'Reload profiles' }).click()
  await expect(page.getByRole('button', { name: 'View Mara Vale' })).toBeVisible()
  await page.getByRole('button', { name: 'Read', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'The garden gate' })).toBeVisible()
})

test('rejects profiles for another book and renders profile content as text', async ({ page }) => {
  await page.route('**/api/runs/cast-book/characters', (route) =>
    route.fulfill({ json: [{ ...profiles[0], book_id: 'wrong-book' }] }),
  )
  await page.goto('/?run=cast-book')
  await page.getByRole('button', { name: 'Characters', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('different book')
  const markup = '<img src=x onerror="window.profileScriptRan=true">'
  await page.route('**/api/runs/cast-book/characters', (route) =>
    route.fulfill({ json: [{ ...profiles[0], character_profile_description: markup }] }),
  )
  await page.getByRole('button', { name: 'Reload profiles' }).click()
  await page.getByRole('button', { name: 'View Mara Vale' }).click()
  await expect(page.getByLabel('In the story')).toContainText(markup)
  await expect(page.locator('.character-profile img')).toHaveCount(0)
})

test('explains standalone imports and supports casts for books without narrative sections', async ({
  page,
}) => {
  await page.goto('/')
  await page.getByLabel('Choose a StoryBook JSON file').setInputFiles({
    name: 'storybook.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(book)),
  })
  await page.getByRole('button', { name: 'Characters', exact: true }).click()
  await expect(
    page.getByRole('heading', { name: 'Open this book from your library' }),
  ).toBeVisible()
  await page.getByRole('button', { name: 'Read', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'The garden gate' })).toBeVisible()
  await page.route('**/api/runs/cast-book/storybook', (route) =>
    route.fulfill({
      json: { ...book, structure: [{ ...book.structure[0], is_part_of_narrative: false }] },
    }),
  )
  await page.goto('/?run=cast-book')
  await expect(page.getByText('This book has no narrative sections to read.')).toBeVisible()
  await page.getByRole('button', { name: 'Characters', exact: true }).click()
  await page.getByRole('button', { name: 'View Mara Vale' }).click()
  await expect(page.getByRole('heading', { name: 'Mara Vale' })).toBeVisible()
})
