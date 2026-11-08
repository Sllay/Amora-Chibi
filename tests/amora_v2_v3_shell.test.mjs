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
  assert.ok(formatted.includes("REJEITADO pelo Core"));
  assert.ok(!formatted.includes("A_virtual_human_v2"));
  assert.ok(!formatted.includes("B_virtual_human_v3"));
});
test("V2 vs V3 jobs are routed to blind display", () => {
  assert.ok(source.includes('job.result?.kind === "amora_v2_v3_ab"'));
  assert.ok(source.includes('formatSocialPersonaABC(job.result)'));
});
