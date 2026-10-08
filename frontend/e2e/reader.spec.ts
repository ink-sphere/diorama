import { test, expect } from '@playwright/test'

const leaf = (title: string, text: string) => ({
  structure_type: 'chapter',
  structure_title: title,
  is_part_of_narrative: true,
  content: [
    { raw_text: `<h1>${title}</h1><p>${text}</p>`, markdown_text: `# ${title}\n\n${text}` },
  ],
})
const book = {
  id: 'test-book',
  metadata: { title: 'The little reading room', authors: ['Diorama test author'] },
  structure: [
    {
      structure_type: 'part',
      structure_title: 'Part one',
      is_part_of_narrative: true,
      content: [
        leaf('An open window', 'The room was quiet. The afternoon light fell across the page.'),
        leaf('A new chapter', 'A new chapter began, and the reader found their place.'),
      ],
    },
  ],
}
const run = {
  runId: 'ebook-test',
  bookId: book.id,
  title: book.metadata.title,
  authors: book.metadata.authors,
  sections: 2,
  modified: '2026-10-07T00:00:00Z',
  coverHref: null,
}

test.beforeEach(async ({ page }) => {
  await page.route('**/api/runs', (route) => route.fulfill({ json: { runs: [run], problems: [] } }))
  await page.route('**/api/runs/ebook-test/storybook', (route) => route.fulfill({ json: book }))
  await page.route('**/api/runs/ebook-test/assets?**', (route) => route.fulfill({ status: 404 }))
})

test('opens a run, navigates nested sections, applies preferences, and resumes', async ({
  page,
}) => {
  await page.goto('/')
  await page.getByRole('button', { name: /The little reading room/ }).click()
  await expect(page.getByRole('heading', { name: 'An open window' })).toBeVisible()
  await page.getByRole('button', { name: 'A new chapter', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'A new chapter' })).toBeVisible()
  await expect(page).toHaveURL(/section=0.1/)
  await page.getByLabel('Reading settings').click()
  await page.getByRole('button', { name: 'Night', exact: true }).click()
  await expect(page.locator('.reader')).toHaveClass(/theme-night/)
  await page.getByLabel('Text size').focus()
  await page.getByLabel('Text size').press('End')
  await expect(page.locator('.reader')).toHaveCSS('--reading-size', '28px')
  await page.getByRole('button', { name: 'Back to library' }).click()
  await expect(page.getByRole('button', { name: /Continue reading/ })).toBeVisible()
  await page.getByRole('button', { name: /Continue reading/ }).click()
  await expect(page.getByRole('heading', { name: 'A new chapter' })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: 'A new chapter' })).toBeVisible()
})

test('opens standalone JSON and explains malformed files', async ({ page }) => {
  await page.goto('/')
  const picker = page.getByLabel('Choose a StoryBook JSON file')
  await picker.setInputFiles({
    name: 'invalid.json',
    mimeType: 'application/json',
    buffer: Buffer.from('{'),
  })
  await expect(page.getByRole('alert')).toContainText('not valid JSON')
  await picker.setInputFiles({
    name: 'storybook.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(book)),
  })
  await expect(page.getByRole('heading', { name: 'An open window' })).toBeVisible()
})

test('mobile contents and reading column fit the viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  await page.getByRole('button', { name: /The little reading room/ }).click()
  await expect(page.getByRole('heading', { name: 'An open window' })).toBeVisible()
  await expect(page.locator('.reader-sidebar')).not.toBeVisible()
  await page.getByRole('button', { name: 'Toggle contents' }).click()
  await expect(page.locator('.reader-sidebar')).toBeVisible()
  await page.getByRole('button', { name: 'A new chapter', exact: true }).click()
  await expect(page.locator('.reader-sidebar')).not.toBeVisible()
  await expect(page.getByRole('heading', { name: 'A new chapter' })).toBeVisible()
  const width = await page.evaluate(() => ({
    body: document.documentElement.scrollWidth,
    viewport: innerWidth,
  }))
  expect(width.body).toBeLessThanOrEqual(width.viewport)
})

test('preserves scroll when selecting the active section or leaving in the same frame', async ({
  page,
}) => {
  const longBook = structuredClone(book)
  longBook.structure[0].content[0].content[0].raw_text =
    '<h1>An open window</h1>' +
    Array.from(
      { length: 100 },
      (_, index) =>
        `<p>Reading paragraph ${index}. The room was quiet and the afternoon light fell across the page.</p>`,
    ).join('')
  await page.route('**/api/runs/ebook-test/storybook', (route) => route.fulfill({ json: longBook }))
  await page.goto('/?run=ebook-test')
  await expect(page.getByRole('heading', { name: 'An open window' })).toBeVisible()
  const pane = page.locator('.reading-pane')
  await pane.evaluate((element) => {
    element.scrollTop = (element.scrollHeight - element.clientHeight) * 0.45
  })
  await page.getByRole('button', { name: 'An open window', exact: true }).click()
  await expect
    .poll(async () =>
      pane.evaluate((element) => element.scrollTop / (element.scrollHeight - element.clientHeight)),
    )
    .toBeCloseTo(0.45, 1)
  await pane.evaluate((element) => {
    element.scrollTop = (element.scrollHeight - element.clientHeight) * 0.6
    document.querySelector<HTMLButtonElement>('[aria-label="Back to library"]')!.click()
  })
  await page.getByRole('button', { name: /Continue reading/ }).click()
  await expect(page.getByRole('heading', { name: 'An open window' })).toBeVisible()
  await expect
    .poll(async () =>
      pane.evaluate((element) => element.scrollTop / (element.scrollHeight - element.clientHeight)),
    )
    .toBeCloseTo(0.6, 1)
  await page.getByRole('button', { name: 'A new chapter', exact: true }).click()
  await page.getByRole('button', { name: 'Back to library' }).click()
  await page.getByRole('button', { name: /Continue reading/ }).click()
  await expect(page.getByRole('heading', { name: 'A new chapter' })).toBeVisible()
})

test('navigates parts, chapters, and scenes with selectable parents and breadcrumbs', async ({
  page,
}) => {
  const nestedBook = {
    ...book,
    structure: [
      {
        structure_type: 'part',
        structure_title: 'Part one',
        is_part_of_narrative: true,
        content: [
          {
            structure_type: 'chapter',
            structure_title: 'Chapter one',
            is_part_of_narrative: true,
            content: [
              { ...leaf('Scene one', 'The first scene begins.'), structure_type: 'scene' },
              { ...leaf('Scene two', 'The second scene follows.'), structure_type: 'scene' },
            ],
          },
          leaf('Chapter two', 'The next chapter begins.'),
        ],
      },
      {
        ...leaf('Appendix', 'Additional notes.'),
        structure_type: 'appendix',
        is_part_of_narrative: false,
      },
    ],
  }
  await page.route('**/api/runs/ebook-test/storybook', (route) =>
    route.fulfill({ json: nestedBook }),
  )
  await page.goto('/?run=ebook-test')
  await expect(page.getByRole('heading', { name: 'Scene one' })).toBeVisible()
  await page.getByRole('button', { name: 'Part one', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Part one' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Open Chapter one' })).toBeVisible()
  await page.getByRole('button', { name: 'Open Chapter one' }).click()
  await expect(page.getByRole('heading', { name: 'Chapter one' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Open Scene two' })).toBeVisible()
  await expect(page).toHaveURL(/section=0.0$/)
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Chapter one' })).toBeVisible()
  await page.getByRole('button', { name: 'Open Scene two' }).click()
  await expect(page.getByRole('heading', { name: 'Scene two' })).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Structure breadcrumb' })).toContainText(
    'Part one',
  )
  await expect(page.getByRole('navigation', { name: 'Structure breadcrumb' })).toContainText(
    'Chapter one',
  )
  await page.getByRole('button', { name: 'Next section', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Chapter two' })).toBeVisible()
  await page.getByRole('button', { name: 'Go to Part one' }).click()
  await page.getByRole('button', { name: /Read from beginning/ }).click()
  await expect(page.getByRole('heading', { name: 'Scene one' })).toBeVisible()
  await page.getByRole('button', { name: 'Collapse Chapter one' }).click()
  await expect(page.getByRole('button', { name: 'Scene two', exact: true })).not.toBeVisible()
  await page.getByRole('button', { name: 'Expand Chapter one' }).click()
  await expect(page.getByRole('button', { name: 'Scene two', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Appendix', exact: true })).toHaveCount(0)
  await expect(page.locator('.sidebar-caption')).toContainText('3 sections')
})

test('hides supplementary nodes in contents, overviews, and reading navigation', async ({
  page,
}) => {
  const supplementary = { ...leaf('Notes', 'Supplementary notes.'), is_part_of_narrative: false }
  const mixedBook = {
    ...book,
    structure: [
      supplementary,
      {
        ...book.structure[0],
        content: [
          supplementary,
          leaf('First chapter', 'The story begins.'),
          {
            ...supplementary,
            structure_title: 'Hidden branch',
            content: [leaf('Hidden child', 'Text')],
          },
          leaf('Second chapter', 'The story continues.'),
          supplementary,
        ],
      },
      supplementary,
    ],
  }
  await page.route('**/api/runs/ebook-test/storybook', (route) =>
    route.fulfill({ json: mixedBook }),
  )
  await page.goto('/?run=ebook-test&section=0')
  await expect(page.getByRole('heading', { name: 'First chapter' })).toBeVisible()
  await expect(page).toHaveURL(/section=1.1$/)
  await expect(page.locator('.sidebar-caption')).toContainText('2 sections')
  await expect(page.getByRole('button', { name: 'Notes', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Hidden branch', exact: true })).toHaveCount(0)
  await expect(page.getByLabel('Previous section', { exact: true })).toBeDisabled()
  await page.getByRole('button', { name: 'Go to Part one' }).click()
  await expect(page.locator('.overview-children')).toContainText('First chapter')
  await expect(page.locator('.overview-children')).not.toContainText('Notes')
  await expect(page.locator('.overview-children')).not.toContainText('Hidden branch')
  await page.getByRole('button', { name: /Read from beginning/ }).click()
  await page.getByRole('button', { name: 'Next section', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Second chapter' })).toBeVisible()
  await expect(page).toHaveURL(/section=1.3$/)
  await expect(page.getByLabel('Next section', { exact: true })).toBeDisabled()
  await expect(page.locator('.step-controls')).toContainText('2 / 2')
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Second chapter' })).toBeVisible()
})

test('falls back from a saved supplementary position to the first narrative section', async ({
  page,
}) => {
  const mixedBook = {
    ...book,
    structure: [
      { ...leaf('Notes', 'Supplementary notes.'), is_part_of_narrative: false },
      ...book.structure,
    ],
  }
  await page.route('**/api/runs/ebook-test/storybook', (route) =>
    route.fulfill({ json: mixedBook }),
  )
  await page.addInitScript(() => {
    localStorage.setItem(
      'diorama:v1:position:test-book:ebook-test',
      JSON.stringify({ path: '0', ratio: 0.8, updated: Date.now() }),
    )
  })
  await page.goto('/?run=ebook-test')
  await expect(page.getByRole('heading', { name: 'An open window' })).toBeVisible()
  await expect(page).toHaveURL(/section=1.0$/)
})

test('handles books with only supplementary nodes for runs and standalone files', async ({
  page,
}) => {
  const supplementaryBook = {
    ...book,
    structure: [{ ...leaf('Notes', 'Supplementary notes.'), is_part_of_narrative: false }],
  }
  await page.route('**/api/runs/ebook-test/storybook', (route) =>
    route.fulfill({ json: supplementaryBook }),
  )
  await page.goto('/?run=ebook-test&section=0')
  await expect(page.getByText('This book has no narrative sections to read.')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Notes' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Back to library' }).click()
  await page.getByLabel('Choose a StoryBook JSON file').setInputFiles({
    name: 'storybook.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(supplementaryBook)),
  })
  await expect(page.getByText('This book has no narrative sections to read.')).toBeVisible()
  await page.getByRole('button', { name: 'Back to library' }).click()
  await expect(page.getByLabel('Choose a StoryBook JSON file')).toBeAttached()
})

test('opens parent overviews from mobile contents and remembers the selected group', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/?run=ebook-test')
  await expect(page.getByRole('heading', { name: 'An open window' })).toBeVisible()
  await page.getByRole('button', { name: 'Toggle contents' }).click()
  await page.getByRole('button', { name: 'Part one', exact: true }).click()
  await expect(page.locator('.reader-sidebar')).not.toBeVisible()
  await expect(page.getByRole('heading', { name: 'Part one' })).toBeVisible()
  await page.getByRole('button', { name: 'Back to library' }).click()
  await page.getByRole('button', { name: /Continue reading/ }).click()
  await expect(page.getByRole('heading', { name: 'Part one' })).toBeVisible()
  await page.getByRole('button', { name: 'Open A new chapter' }).click()
  await expect(page.getByRole('heading', { name: 'A new chapter' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Go to Part one' })).toBeVisible()
  const width = await page.evaluate(() => ({
    body: document.documentElement.scrollWidth,
    viewport: innerWidth,
  }))
  expect(width.body).toBeLessThanOrEqual(width.viewport)
})
