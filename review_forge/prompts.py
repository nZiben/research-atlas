QUERIES = """Propose 3–6 complementary, concise English keyword queries for the supplied research topic and questions.
Use plain phrases, no provider-specific operators, paper titles, author lists or invented references.
Cover common synonyms, competing approaches, evaluation and failure modes. Queries are search proposals,
not scientific findings. If the scope is too broad for the given paper budget, say so in scope_note;
do not silently replace the user's topic with a different one. Do not change inclusion/exclusion criteria.
Return {"queries":["English keyword phrase"],"scope_note":"scope advice in the review language"}.
"""

SCREEN = """Screen every candidate against the review questions and explicit inclusion/exclusion criteria.
Classify core (direct evidence), external (useful context), or exclude. Abstract-based screening is provisional.
Priority is topical relevance 0–100, NOT scientific quality or citation popularity. Give a concrete reason.
Never infer peer-review status or paper contents absent from supplied metadata.
Schema: {"decisions":[{"paper_id":"supplied ID","decision":"core|external|exclude","reason":"...","priority":90}]}.
Return all and only supplied paper IDs, once each. Use the review language for prose.
"""

EXTRACT = """Extract up to LIMIT scientifically meaningful claims from this supplied source chunk.
Return zero claims for bibliography, boilerplate or irrelevant text. Each claim must be supported by a short
EXACT contiguous quote from a supplied segment; preserve case, hedges, numbers and conditions. No ellipses.
Do not extract claims from a reference list or attribute later work to this paper. Do not treat related-work
claims as this paper's experiments. Prefer primary results, definitions, counterexamples and explicit limits.
If this is abstract-only, do not infer unreported details, negative results or absent limitations.
kind: empirical (observation), theoretical (formal result with assumptions), hypothesis (author conjecture),
interpretation (author explanation). Preserve the distinction. Include dataset, task, metric, units, hardware,
precision, baseline and evaluation protocol in conditions/text WHEN PROVIDED; never fill missing values.
Put only source-stated limitations in limitations. Extraction uncertainty is not an author's limitation.
Schema: {"claims":[{"text":"one self-contained bounded claim","kind":"empirical|theoretical|hypothesis|interpretation",
"conditions":["..."],"limitations":["..."],"concepts":["canonical English concept label"],
"evidence":[{"segment_id":"supplied ID","quote":"exact source-language quote within word limit"}]}]}.
Use the review language for text/conditions/limitations; keep quotes in their original language.
"""

PICK = """Select the supplied claim IDs most useful for cross-paper synthesis, up to the specified cap.
Balance definitions, positive and negative findings, limitations, competing mechanisms, historical origins
and relevance to the review questions. Do not choose only positive results. All extracted claims remain in
the paper card; this is explicitly a compressed evidence selection, not proof of corpus completeness.
Return {"claim_ids":["supplied ID"],"reason":"selection rationale in review language"}.
"""

SYNTHESIZE = """Build a conceptual review using ONLY supplied claim records. It must organize ideas across papers,
not merely concatenate abstracts. No external knowledge, invented references, rankings, results or figures.
Use the review language. Preserve author hedges, conditions, chronology, and core vs external distinction.
Every scientific statement must cite supplied claim_ids. Cite several papers for cross-paper synthesis.
Do not compare metrics across incompatible datasets, splits, hardware, precision, budgets or protocols.
Do not assert consensus from citation counts, search coverage, or the absence of disagreement in this sample.
If evidence is insufficient, describe precisely what this corpus does/does not establish with relevant IDs.
An empty disagreements array is valid; do not invent controversy. Distinguish genuinely conflicting results
from different definitions, assumptions or experimental regimes. Propose research ideas as editorial only.
Merge synonyms into canonical concepts, retain aliases, distinguish a definition from causal explanation.
Scope and corpus statistics will be rendered from metadata; do not invent them.
Each statement has {"text":"...","claim_ids":["supplied ID"],"kind":"evidence"}.
Only gaps and reading_path sections may contain kind=editorial (recommendations/questions, clearly labeled).
Required sections, in order: overview, taxonomy, timeline, methods, comparison, disagreements, gaps, reading_path.
Include at least one item in each; lack of evidence must be expressed as a bounded observation about cited claims.
Schema: {"title":"...","sections":[{"key":"overview","title":"...","items":[STATEMENT]}, ...],
"concepts":[{"id":"unique-ascii-slug-except-index","name":"...","aliases":["..."],"definition":STATEMENT,
"statements":[STATEMENT],"disagreements":[STATEMENT]}],
"relations":[{"source":"concept-id","target":"concept-id","kind":"prerequisite|related|disagrees",
"statement":STATEMENT}]}.
For prerequisite, source is the prerequisite of target. Do not create edges without evidence.
"""

AUDIT = """Critique the supplied synthesis against the supplied extracted evidence; do not rewrite it.
Check: causal overreach, loss of hedging, incorrect attribution, historical anachronism, treating hypotheses
as facts, false consensus/controversy, incompatible metric comparisons, abstract-only overreach and missing
negative findings. A citation ID alone does not prove entailment. You cannot verify an original figure,
full derivation or omitted source with this context. Report limitations rather than pretending you did.
Return {"findings":[{"severity":"high|medium|low","location":"concept/section identifier",
"issue":"concrete issue and suggested correction in review language","claim_ids":["supplied IDs"]}]}.
An empty list means this model reported no findings; it is not a human quality approval.
"""
