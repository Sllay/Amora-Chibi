import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import assert from "node:assert/strict";
import test from "node:test";

const source = readFileSync(new URL("../web/amora-shell.js", import.meta.url), "utf8");
const formatter = runInNewContext(
  source.slice(
    source.indexOf("function formatSocialPersonaABC("),
    source.indexOf("function formatPartialProgress("),
  ) + "\nformatSocialPersonaABC",
  { seconds: (ms) => (ms / 1000).toFixed(2) + "s" },
);

test("V2 × V3 has blind candidates and full untrimmed text", () => {
  const output = "Puta merda, esse chat só fala sacanagem. ".repeat(35);
  const formatted = formatter({
    kind: "amora_v2_v3_ab",
    case: "sacanagem",
    variants: [
      { candidate_id: "candidate-1", validated: true, text: output, latency_ms: 7800 },
      { candidate_id: "candidate-2", validated: false,
        candidate_output: '{"moves":', error_code: "EXPERIMENT_JSON_INVALID" },
    ],
    rubric: { "ousadia sem convite": null },
    reveal_token: "opaque",
  });
  assert.ok(formatted.includes("Amora V2 × V3"));
  assert.ok(formatted.includes(output));
  assert.ok(formatted.includes('{"moves":'));
  assert.ok(formatted.includes("JSON INVÁLIDO · Core não executado"));
  assert.ok(!formatted.includes("A_virtual_human_v2"));
  assert.ok(!formatted.includes("B_virtual_human_v3"));
});
test("V2 vs V3 jobs are routed to blind display", () => {
  assert.ok(source.includes('job.result?.kind === "amora_v2_v3_ab"'));
  assert.ok(source.includes('formatSocialPersonaABC(job.result)'));
});

test("NVIDIA timeout is displayed as a transport failure, never Core refusal", () => {
  const result = formatter({
    kind: "amora_v2_v3_ab",
    case: "sacanagem",
    variants: [
      {
        candidate_id: "candidate-1",
        validated: false,
        validation_status: "not_run",
        error_code: "EXPERIMENT_NVIDIA_TIMEOUT",
        failure_stage: "waiting_first_event",
        timeout_kind: "idle",
        stream_diagnostics: {elapsed_ms: 55090, first_visible_ms: null},
        latency_ms: 55090,
      },
      {
        candidate_id: "candidate-2",
        validated: false,
        validation_status: "rejected_by_core",
        candidate_output: '{"moves":[],"closing":"statement"}',
        error_code: "EMPTY_REALIZATION",
      },
    ],
    transport_deadlines: {
      idle_seconds: 100,
      max_total_seconds: 225,
      production_unchanged: true,
    },
    rubric: {},
    reveal_token: "secret-looking-but-not-sensitive",
  });
  assert.ok(result.includes("TIMEOUT NVIDIA · Core não executado"));
  assert.ok(result.includes("Nenhuma fala recebida da NVIDIA"));
  assert.ok(result.includes("etapa=waiting_first_event · timeout=idle"));
  assert.ok(result.includes("REJEITADO pelo Core"));
  assert.ok(result.includes("idle=100s · teto=225s"));
  assert.ok(!result.includes("candidate-1 · REJEITADO pelo Core"));
});
