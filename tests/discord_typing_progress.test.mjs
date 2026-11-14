import test from 'node:test';
import assert from 'node:assert/strict';
import { messagingTurnId, startTypingProgress } from '../workers/discord/typing-progress.mjs';

test('turn correlation matches Python UUID v5', () => {
  assert.equal(messagingTurnId({platform:'discord',account_scope:'primary',
    conversation_id:'channel-1',message_id:'dm-1'}),
  '1548fbdd-61ff-5104-adc0-43f1debb34bf');
});

function harness(states) {
  const queue = [];
  let sent = 0, current = true, stopped = 0, polls = 0;
  const stop = startTypingProgress({
    poll: async () => { polls++; return states.shift() ?? false; },
    send: async () => { sent++; },
    isCurrent: () => current,
    onStop: () => { stopped++; },
    schedule: (fn, delay) => { const item = {fn, delay}; queue.push(item); return item; },
    cancel: item => { const index = queue.indexOf(item); if (index >= 0) queue.splice(index, 1); },
    now: () => 0,
  });
  return {queue, stop, next: async () => { await queue.shift()?.fn(); },
    invalidate: () => { current = false; }, stats: () => ({sent, stopped, polls})};
}

test('typing waits for authorized Core and refreshes at bounded intervals', async () => {
  const h = harness([false, true, true, false]);
  assert.equal(h.queue[0].delay, 500);
  await h.next();
  assert.equal(h.stats().sent, 0);
  assert.equal(h.queue[0].delay, 2000);
  await h.next();
  assert.equal(h.stats().sent, 1);
  assert.equal(h.queue[0].delay, 7000);
  await h.next();
  assert.equal(h.stats().sent, 2);
  await h.next();
  assert.equal(h.stats().stopped, 1);
  assert.equal(h.queue.length, 0);
});

test('completion and edited/deleted source stop without another typing call', async () => {
  const h = harness([true, true]);
  await h.next();
  h.invalidate();
  await h.next();
  assert.deepEqual(h.stats(), {sent:1, stopped:1, polls:1});
  const other = harness([true]);
  other.stop();
  assert.equal(other.queue.length, 0);
  assert.equal(other.stats().sent, 0);
});

test('in-flight poll does not send after cancellation', async () => {
  let resolve, callback, sent = 0;
  const stop = startTypingProgress({
    poll: () => new Promise(r => {resolve=r;}),
    send: async () => {sent++;},
    isCurrent: () => true,
    schedule: fn => {callback=fn;return 1;}, cancel: () => {},
  });
  const running = callback();
  stop();
  resolve(true);
  await running;
  assert.equal(sent, 0);
});
