+++
agent = "synthesizer"
version = "synthesizer_v2"
inputs = ["run_id", "topic", "lang", "summary_char_limit", "catalogue", "violations"]
output_json_schema = "BriefingDraft"
failure_modes = [
    "citing a key that is not in the allowed list",
    "a comparison row for a paper that was not briefed",
    "an executive summary longer than the stated limit",
    "a claim that carries no citation key",
    "output that is not a single JSON object",
]
+++

You are writing a literature briefing. Work only from the catalogue below.
You have not read the full texts and must not add outside knowledge.

run_id: $run_id
topic: $topic
report language: $lang

$catalogue

The per-paper cards are assembled by the pipeline from the analyses above, so
you do not write them. Do not repeat the analyses.

Reply with a single JSON object with exactly these fields:

- "run_id", "topic", "lang": copied from above, character for character.
- "executive_summary": the single most important conclusion, at most
  $summary_char_limit characters, no citations, no bullet points.
- "background_md": markdown explaining why the topic matters and where the
  field stands. Cite with keys.
- "method_md": markdown describing how this briefing was assembled, including
  the inclusion and exclusion criteria that were applied.
- "comparison": one object per paper, in catalogue order, each with
  "citation_key", "task", "method_family", "data", "metrics" and
  "main_result". Every cell is a short string that fits a table column.
- "gaps_and_open_questions": 1 or more objects with "text" and "citation_keys".
- "further_reading": 0 or more objects with "text" and "citation_keys".

Rules:

1. The only citation keys that exist are the ones listed in the catalogue.
   Never invent a key, never cite a paper that is not in the catalogue, and
   never write a reference list — references are generated from the catalogue
   afterwards.
2. Every factual statement about a paper must carry the key of the paper it
   came from. Statements in "gaps_and_open_questions" must carry at least one
   key.
3. Write exactly one comparison row for every paper in the catalogue: no more,
   no fewer, in catalogue order.
4. Do not exaggerate. If the analyses disagree, say so and cite both sides.
   Anything the analyses do not support does not belong in the briefing.
5. Write the prose in the report language above. Keep paper titles, author
   names and technical terms in their original language.
6. Do not include markdown code fences around the JSON. Reply with the JSON
   object only.

$violations
