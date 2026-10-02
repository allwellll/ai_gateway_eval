import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { endpointFor } from '../web/lib/routing.js';
const cases = JSON.parse(readFileSync(new URL('./routing-cases.json', import.meta.url)));
for (const [url, model, expected] of cases) test(`route ${url} → ${model}`, () => assert.equal(endpointFor(url, model), expected));
for (const url of ['https://user:pass@example.com', 'https://example.com?key=secret', 'https://example.com/#x', 'file:///etc/passwd']) {
  test(`reject credentials / unsupported URLs: ${url}`, () => assert.throws(() => endpointFor(url, 'gpt-6-astra')));
}
