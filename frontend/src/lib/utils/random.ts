const TOKEN_CHARS = 'abcdefghijklmnopqrstuvwxyz0123456789';

function assertLength(length: number): void {
  if (!Number.isInteger(length) || length <= 0) {
    throw new Error(`makeUniqueToken length must be a positive integer, received ${length}`);
  }
}

export function makeUniqueToken(length: number = 16): string {
  assertLength(length);

  let token = '';
  for (let index = 0; index < length; index += 1) {
    const charIndex = Math.floor(Math.random() * TOKEN_CHARS.length);
    token += TOKEN_CHARS.charAt(charIndex);
  }
  return token;
}
