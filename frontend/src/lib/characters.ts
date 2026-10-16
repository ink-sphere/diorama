export interface CharacterProfile {
  id: string
  book_id: string
  character_name: string
  character_alias: string[]
  character_profile_description: string
  headshot_visual_prompt: string
}

export function parseCharacterProfiles(value: unknown, bookId: string): CharacterProfile[] {
  if (!Array.isArray(value) || value.length > 20000)
    throw new Error('Character profiles must be a list of at most 20,000 characters.')
  const ids = new Set<string>()
  return value.map((item, index) => {
    if (!item || typeof item !== 'object' || Array.isArray(item))
      throw new Error(`Character ${index + 1} is not a valid profile.`)
    const textFields = [
      'id',
      'book_id',
      'character_name',
      'character_profile_description',
      'headshot_visual_prompt',
    ] as const
    for (const field of textFields) {
      if (typeof item[field] !== 'string' || !item[field].trim())
        throw new Error(`Character ${index + 1} needs a non-empty ${field}.`)
    }
    if (item.book_id !== bookId)
      throw new Error('These character profiles belong to a different book.')
    if (ids.has(item.id)) throw new Error('Character profiles contain a repeated character ID.')
    ids.add(item.id)
    if (
      !Array.isArray(item.character_alias) ||
      item.character_alias.some((alias: unknown) => typeof alias !== 'string' || !alias.trim())
    )
      throw new Error(`Character ${index + 1} needs a list of aliases.`)
    return {
      id: item.id,
      book_id: item.book_id,
      character_name: item.character_name,
      character_alias: [...item.character_alias],
      character_profile_description: item.character_profile_description,
      headshot_visual_prompt: item.headshot_visual_prompt,
    }
  })
}

function searchKey(value: string): string {
  return value
    .normalize('NFKD')
    .replace(/\p{Diacritic}/gu, '')
    .toLocaleLowerCase()
    .trim()
}

export function searchCharacters(profiles: CharacterProfile[], query: string): CharacterProfile[] {
  const key = searchKey(query)
  return profiles.filter((profile) =>
    [profile.character_name, ...profile.character_alias].some((name) =>
      searchKey(name).includes(key),
    ),
  )
}
