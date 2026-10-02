import test from 'node:test';
import assert from 'node:assert/strict';
import { orderedEvents, eventDay, fingerprintStatus, candyStatus, normalizedVerdict } from '../web/analytics.js';

test('Shanghai dates and date-only records have deterministic sequence order', () => {
  const events = [
    { id: 7, time_precision: 'date', tested_date: '2026-10-02' },
    { id: 6, time_precision: 'date', tested_date: '2026-10-02' },
    { id: 4, tested_at: '2026-10-01T18:00:00Z', tested_date: '2000-01-01' },
    { id: 1, tested_at: '2026-10-01T14:00:00Z' },
    { id: 5, tested_at: '2026-10-01T18:00:00Z' },
  ];
  assert.equal(eventDay(events[2]), '2026-10-02');
  assert.deepEqual(orderedEvents(events).map(event => event.id), [1, 4, 5, 6, 7]);
});

test('sequence tracks preserve identity and reasoning as separate signals', () => {
  const event = { verdict: '真', candy_accuracy: .2, candy_answers: '28,28,28,28,21' };
  assert.equal(normalizedVerdict(event), '存疑');
  assert.equal(fingerprintStatus(event), 'warning');
  assert.equal(candyStatus(event), 'warning');
  assert.equal(candyStatus({ candy_accuracy: 1, candy_answers: '21,?' }), 'warning');
  assert.equal(candyStatus({ candy_accuracy: null }), 'unknown');
  assert.equal(fingerprintStatus({ verdict: '无法评测' }), 'unknown');
});
