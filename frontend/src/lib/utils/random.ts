const TOKEN_CHARS = 'abcdefghijklmnopqrstuvwxyz0123456789';
// A random byte at or above this bound is drawn again, so each character is equally likely.
const BYTE_BOUND = 256 - (256 % TOKEN_CHARS.length);

function assertLength(length: number): void {
  if (!Number.isInteger(length) || length <= 0) {
    throw new Error(`makeUniqueToken length must be a positive integer, received ${length}`);
  }
}

/**
 * A token of `length` lowercase letters and digits, drawn from the platform's
 * CSPRNG (`crypto.getRandomValues`, which needs no secure context, unlike
 * `crypto.randomUUID`). SSE request ids come from here: the backend sends a
 * connection's events by its id, so an id must not be guessable.
 */
export function makeUniqueToken(length: number = 16): string {
  assertLength(length);

  let token = '';
  const bytes = new Uint8Array(length);
  while (token.length < length) {
    crypto.getRandomValues(bytes);
    for (const byte of bytes) {
      if (byte < BYTE_BOUND && token.length < length) {
        token += TOKEN_CHARS.charAt(byte % TOKEN_CHARS.length);
      }
    }
  }
  return token;
}
