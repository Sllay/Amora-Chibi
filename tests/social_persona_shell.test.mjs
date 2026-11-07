import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import assert from 'node:assert/strict';
import test from 'node:test';

const source = readFileSync(new URL('../web/amora-shell.js', import.meta.url), 'utf8');
const render = runInNewContext(source.slice(source.indexOf('function formatSocialPersonaABC('), source.indexOf('function formatPartialProgress(')) + '\nformatSocialPersonaABC', { seconds: () => '1s' });

test('blind shell displays full valid and rejected outputs without model labels', () => {
  const text = 'Uma fala completa. '.repeat(80);
  const report = render({ case: 'bug', variants: [
    { candidate_id: 'candidate-1', validated: true, text },
    { candidate_id: 'candidate-2', validated: false, candidate_output: 'JSON inválido', error_code: 'EXPERIMENT_JSON_INVALID' },
  ], rubric: { naturalidade: null }, reveal_token: 'opaque' });
  assert.ok(report.includes(text));
  assert.ok(report.includes('REJEITADO pelo Core'));
  assert.ok(report.includes('JSON inválido'));
  assert.ok(!report.includes('Venice'));
  assert.ok(!report.includes('Mistral'));
});
