import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import assert from "node:assert/strict";
import test from "node:test";

const source = readFileSync(new URL("../web/amora-shell.js", import.meta.url), "utf8");
const formatter = runInNewContext(
  source.slice(source.indexOf("function formatSocialPersonaABC("),
               source.indexOf("function formatPartialProgress(")) +
  "\nformatSocialPersonaABC",
  { seconds: (ms) => `${(ms / 1000).toFixed(2)}s` },
);

test("V2 A/B has readable full blind candidates, no exposed provider labels", () => {
  const text = "Puta merda, foi o cabo mais teimoso do mundo! ".repeat(45);
  const formatted = formatter({
    kind: "amora_v2_ab",
    case: "xingamento",
    variants: [
      { candidate_id: "candidate-1", validated: true, text, latency_ms: 8500 },
      { candidate_id: "candidate-2", validated: false,
        candidate_output: "non JSON", error_code: "EXPERIMENT_JSON_INVALID" },
    ],
    rubric: { "malícia contextual": null },
    reveal_token: "opaque",
  });
  assert.ok(formatted.includes("Amora Virtual Human V2"));
  assert.ok(formatted.includes(text));
  assert.ok(formatted.includes("JSON INVÁLIDO · Core não executado"));
  assert.ok(!formatted.includes("REJEITADO pelo Core"));
  assert.ok(formatted.includes("non JSON"));
  assert.ok(!formatted.includes("Mistral"));
  assert.ok(!formatted.includes("A_core"));
  assert.ok(!formatted.includes("B_virtual_human_v2"));
});

test("V2 job is routed to blind format and never falls back to raw JSON", () => {
  assert.ok(source.includes('job.result?.kind === "amora_v2_ab"'));
  assert.ok(source.includes("formatSocialPersonaABC(job.result)"));
});
