import { parse, parseNumberAndBigInt, stringify } from 'lossless-json';
// lossless-json 4.3.3 (MIT), https://github.com/josdejong/lossless-json.
// One parser for HTTP, text edits and JSONEditor; never coerce large integers to Number.
export type RuntimeJson = null | boolean | string | number | bigint | RuntimeJson[] | { [key: string]: RuntimeJson };
export const parseRuntime = (text: string): RuntimeJson => parse(text, undefined, parseNumberAndBigInt) as RuntimeJson;
export const stringifyRuntime = (value: unknown, space?: number): string => stringify(value, undefined, space) ?? '';
export function parseObject(text: string): Record<string, unknown> {
  const value = parseRuntime(text);
  if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new Error('必须是有效 JSON 对象');
  return value as Record<string, unknown>;
}
