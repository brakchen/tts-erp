import { parse, parseNumberAndBigInt } from 'lossless-json';

/** Identity/count DTOs only, not configuration or financial JSON.
 * lossless-json 4.3.3, MIT: https://github.com/josdejong/lossless-json
 * The maintained parser produces bigint for every integer; retain the ordinary
 * type for safe counters and decimal string identity for oversized integers.
 * This must run on response text, before native JSON.parse can lose digits.
 */
export function parseIdentityJson(text: string): unknown {
  return parse(text, (_key, value) => typeof value === 'bigint'
    ? Number.isSafeInteger(Number(value)) ? Number(value) : value.toString()
    : value, parseNumberAndBigInt);
}
