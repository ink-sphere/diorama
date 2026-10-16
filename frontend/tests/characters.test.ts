import { describe, expect, it } from 'vitest'
import { parseCharacterProfiles, searchCharacters } from '../src/lib/characters'

const profile = {
  id: 'mara',
  book_id: 'book',
  character_name: 'Mára Vale',
  character_alias: ['The Keeper'],
  character_profile_description: 'Mara tends the lantern house.',
  headshot_visual_prompt: 'Her appearance is unspecified.',
}

describe('character profiles', () => {
  it('accepts the Python profile schema and empty casts without changing source data', () => {
    const input = [structuredClone(profile)]
    const parsed = parseCharacterProfiles(input, 'book')
    expect(parsed).toEqual(input)
    parsed[0].character_alias.push('New alias')
    expect(input[0].character_alias).toEqual(['The Keeper'])
    expect(parseCharacterProfiles([], 'book')).toEqual([])
  })

  it.each([
    null,
    {},
    '[]',
    [null],
    [{ ...profile, character_name: ' ' }],
    [{ ...profile, character_alias: 'alias' }],
    [{ ...profile, character_alias: [null] }],
    [{ ...profile, character_profile_description: null }],
    [{ ...profile, headshot_visual_prompt: undefined }],
  ])('rejects malformed profiles: %j', (input) => {
    expect(() => parseCharacterProfiles(input, 'book')).toThrow()
  })

  it('rejects profiles from another book and duplicate IDs, but permits shared names', () => {
    expect(() => parseCharacterProfiles([profile], 'other-book')).toThrow('different book')
    expect(() => parseCharacterProfiles([profile, profile], 'book')).toThrow(
      'repeated character ID',
    )
    expect(parseCharacterProfiles([profile, { ...profile, id: 'other' }], 'book')).toHaveLength(2)
  })

  it('searches names and aliases without case or accent sensitivity', () => {
    const profiles = parseCharacterProfiles([profile], 'book')
    expect(searchCharacters(profiles, '  MARA  ')).toEqual(profiles)
    expect(searchCharacters(profiles, 'keeper')).toEqual(profiles)
    expect(searchCharacters(profiles, ' ')).toEqual(profiles)
    expect(searchCharacters(profiles, 'lantern')).toEqual([])
  })
})
