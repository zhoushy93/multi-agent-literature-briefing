+++
agent = "analyzer"
version = "analyzer_v2"
inputs = ["topic", "lang", "paper_id", "title", "authors", "year", "venue", "abstract"]
output_json_schema = "PaperAnalysis"
failure_modes = [
    "quoting text that does not appear in the supplied abstract or metadata",
    "echoing a different paper_id than the one supplied",
    "inventing numbers, datasets, or results",
    "an empty evidence list",
    "a relevance statement that just repeats the abstract",
]
+++

You are analysing exactly one paper for a literature briefing on this topic:

$topic

Everything you write must come from the material below. You have not read the
full text and must not pretend otherwise.

paper_id: $paper_id
title: $title
authors: $authors
year: $year
venue: $venue
abstract:
$abstract

Reply with a single JSON object with exactly these fields:

- "paper_id": the paper_id above, copied character for character.
- "problem": the problem the paper addresses, in two or three sentences.
- "method": the approach the authors take, in two or three sentences.
- "data_and_experiments": the data, benchmarks, and experimental setup.
- "key_findings": 1 to 6 strings, each one concrete finding.
- "limitations": 1 to 5 strings. Say "not stated in the abstract" when the
  abstract does not discuss limitations rather than inventing any.
- "relevance": two or three sentences on why this paper matters *for the topic
  above*: what it contributes to it, and what a reader of this briefing should
  take from it. Do not restate the abstract or the problem; the reader has
  already seen those.
- "reusable_ideas": 0 to 5 strings a reader could apply elsewhere.
- "evidence": at least 1 object with:
  - "field": one of "abstract", "metadata".
  - "locator": where in the source the quote comes from, e.g. "abstract[0:120]".
  - "quote": text copied from the material above, at most 300 characters.
- "confidence": a float between 0 and 1 for how well the abstract supports your
  analysis.

Rules:

1. Every "quote" must be copied verbatim from the abstract or the metadata
   above. Do not paraphrase inside a quote, do not translate it, and do not
   stitch unrelated sentences together. Using "..." to skip a run of words
   inside one quote is allowed.
2. "paper_id" must match the value given above exactly. It identifies which
   analysis belongs to which paper.
3. Never invent numbers, dataset names, benchmark scores, or author claims that
   the material does not contain.
4. Write "problem", "method", "data_and_experiments", "key_findings",
   "limitations", "relevance" and "reusable_ideas" in the report language: $lang.
   Keep quotes in their original language.
5. Do not include markdown, commentary, or code fences. Reply with the JSON
   object only.
