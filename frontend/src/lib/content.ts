import DOMPurify from 'dompurify'
import { marked } from 'marked'
import type { Section } from './storybook'

export function imageUrl(runId: string | null, reference: string, cover = false): string | null {
  if (/^data:image\/(?:png|jpe?g|gif|webp|avif);base64,/i.test(reference)) return reference
  if (!runId || /^(?:[a-z][\w+.-]*:|\/\/)/i.test(reference)) return null
  const path = reference.split(/[?#]/)[0].replace(/^(?:\.\.?\/)+/, '')
  return `/api/runs/${encodeURIComponent(runId)}/assets?${new URLSearchParams({ path, ...(cover ? { cover: '1' } : {}) })}`
}

export function renderSection(section: Section, runId: string | null): string {
  const html = section.content
    .map((fragment) =>
      /<\/?[a-z][^>]*>/i.test(fragment.raw_text)
        ? fragment.raw_text
        : marked.parse(fragment.markdown_text, { async: false }),
    )
    .join('\n')
  const sanitized = DOMPurify.sanitize(html, {
    USE_PROFILES: { html: true, svg: true, mathMl: true },
    FORBID_TAGS: [
      'style',
      'form',
      'input',
      'button',
      'iframe',
      'object',
      'embed',
      'audio',
      'video',
    ],
    FORBID_ATTR: ['style', 'srcset', 'autofocus'],
    ALLOW_DATA_ATTR: false,
  })
  const template = document.createElement('template')
  template.innerHTML = sanitized
  template.content.querySelectorAll('img, image').forEach((image) => {
    const reference =
      image.getAttribute('src') ??
      image.getAttribute('href') ??
      image.getAttribute('xlink:href') ??
      ''
    const url = imageUrl(runId, reference)
    if (!url) {
      const placeholder = document.createElement('span')
      placeholder.className = 'missing-image'
      placeholder.textContent = image.getAttribute('alt') || 'Image unavailable in this file'
      image.replaceWith(placeholder)
    } else {
      if (image.tagName.toLowerCase() === 'img') {
        image.setAttribute('src', url)
        image.setAttribute('loading', 'lazy')
        image.setAttribute('decoding', 'async')
      } else {
        image.removeAttribute('xlink:href')
        image.setAttribute('href', url)
      }
    }
  })
  template.content.querySelectorAll('a').forEach((anchor) => {
    const href = anchor.getAttribute('href') ?? ''
    if (/^https?:\/\//i.test(href)) {
      anchor.setAttribute('target', '_blank')
      anchor.setAttribute('rel', 'noopener noreferrer')
    } else if (href.includes('#'))
      anchor.setAttribute('href', `#${href.split('#').slice(1).join('#')}`)
    else anchor.removeAttribute('href')
  })
  template.content.querySelectorAll('use').forEach((element) => {
    for (const attribute of ['href', 'xlink:href']) {
      if (!element.getAttribute(attribute)?.startsWith('#')) element.removeAttribute(attribute)
    }
  })
  return template.innerHTML
}
