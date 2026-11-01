# Validation record — Paper Triage 0.1.0

Date: 2026-11-01. Host: Codex desktop, macOS. Method: explicit loading of `skills/paper-triage/SKILL.md` in the authoring chat, local document tools and source inspection. Reports were drafted and checked by the same assistant. No independent evaluator, fresh-session benchmark or end-user trial was run.

## Real-source checks

| Case | Input and access | Evidence checked | Result |
| --- | --- | --- | --- |
| [Theory](examples/theory.md) | arXiv:2105.05820v2; complete 35-page PDF retrieved; main text pp. 1–15 read with text extraction | Eq. (1), p. 7; rendered Fig. 1, p. 16 and Fig. 2, p. 18 inspected visually | Short reading decision produced; calculations distinguished from experiments; embedded SI explicitly not audited |
| [Experiment](examples/experiment.md) | arXiv:2310.09134v1; HTML main text Sections I–V read; PDF download timed out | Sections III.1–III.2, IV.1–IV.2 and V; Fig. 7 inspected visually; Figs. 4–6 checked through associated text/captions | Short reading decision produced; observations distinguished from phenomenological explanation; separate SI not claimed as read |

Sources: [theory PDF](https://arxiv.org/pdf/2105.05820v2), [experimental HTML](https://arxiv.org/html/2310.09134v1). Examples pin versions so that figure/equation numbering remains reproducible. The experimental HTML includes a generated date that differs from the submission metadata; the report uses the versioned arXiv record. Neither example assesses later revisions or verifies all current material assignments.

### Failures and corrections

- An initial extraction attempt ran while PDF downloads were still incomplete and failed with xref errors. Downloads and parsing were then made sequential. The complete theory PDF passed `pdfinfo` and `pdftotext`; the partial experimental download was not used as evidence.
- The experimental PDF transfer timed out after 90 seconds. The report uses readable HTML and labels this fallback; it does not claim PDF verification.
- Text extraction distorted spin-group notation. The equation and decisive figures were cross-checked against HTML and rendered PDF pages before producing the theory example.
- Full-project `selftest` in a clean public-main checkout reached the existing screening regression guard and failed because its ignored `data/gold` fixtures were absent (`gold 集过小: 0`). That check depends on local research fixtures and is not evidence of a standalone Skill failure. This release does not modify or redistribute those fixtures. The configured personal checkout is checked separately; outcomes are kept distinct in the launch record.

Raw downloads and rendered source figures remain local under ignored `reports/release/paper-triage-eval/`; they are not redistributed in the Skill ZIP or the public repository.

## Boundary walkthroughs

[Synthetic boundary requests and expected-safe responses](examples/boundaries.md) document abstract-only access, absent research goals and instructions embedded in source text. These are same-author walkthroughs, not independent prompt-injection security tests. They identify behavior to re-check when the skill changes.

## Packaging and local setup

- `quick_validate.py` checks Skill identity/frontmatter and unfinished placeholders.
- The standard-library packager includes exactly two allowlisted files: `paper-triage/SKILL.md` and `paper-triage/LICENSE`. It verifies ZIP CRC and byte equality, fixes archive timestamps and refuses to replace an existing file.
- A temporary-directory smoke check builds twice, compares hashes, extracts the package into `.agents/skills/`, checks exact byte equality and checks rejection of replacement/source overwrite.
- The repository-local `.agents/skills/paper-triage` symlink points to the maintained `skills/paper-triage` source; target resolution is checked. Codex auto-discovery is documented by the [official skill guide](https://developers.openai.com/codex/skills#where-to-save-skills), but automatic activation in a fresh session has not been observed in this run.

See [launch record](../SKILL_LAUNCH.md) for the actual check outcomes, public URLs and remaining account steps.

## Limits of this evidence

Two curated, shortened examples establish neither cross-disciplinary accuracy nor superiority to a generic prompt. No success rate, time saving, external user count or conversion claim is supported. Claude/Cursor/ChatGPT installations and Agensi-delivered execution remain untested. The next useful evidence is an unaided researcher completing a second paper with the skill.
