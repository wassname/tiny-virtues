# Rubric v2 results: can Jev tell the traditions apart the way Opus does?

Summary page written by PI/Claude-opus, 2026-10-22. Generated tables: `table.md` (`just rubric-report`), `figure.png`. Supervisor verdicts on 39 disagreement stories: `verdicts.md`. What each virtue tests, per group: `meanings.md`.

Bottom line: rubric v2 (source quotations plus Opus-labelled story examples) makes Jev place the differences between traditions where Opus places them in 12 of 13 groups, with the plain-word control failing everywhere. Eight groups pass, patience shows a source-supported distinction that Jev draws more strongly than Opus, four are unresolved (Jev separates traditions slightly more than Opus in diligence, justice and property; courage shows no gain over the old text), and sexual conduct has too few stories to test. wassname accepted this outcome with the unresolved groups recorded ("yes sign off", 2026-10-22). Known Jev limits are listed in `verdicts.md`.

"Pair r" = average correlation between two traditions' scores within one judge (lower = traditions more separated). "Same pattern as Opus" = after removing each story's mean over the group's virtues, correlation of Jev's scores with Opus's (Opus = 1; the plain word should be about 0). Gap interval: 2,000 story resamples, seed 20261022; it does not measure repeat-call noise. Rubric v2 = loop 4 (wisdom loop 3).

| Group | Stories | Opus pair r | Jev pair r: old text → rubric v2 | Gap to Opus [95%] | Same pattern as Opus: plain word / old text → quotes → rubric v2 | Finding |
|:--|--:|--:|:--|:--|:--|:--|
| patience | 16 | 0.81 | 0.70 → 0.73 | -0.08 [-0.16, -0.00] | -0.06 / 0.62 → 0.69 → 0.71 | distinction supported by source (Jev separates more) |
| compassion | 15 | 0.82 | 0.88 → 0.86 | +0.05 [-0.03, +0.11] | -0.09 / 0.75 → 0.79 → 0.84 | passed |
| anger | 19 | 0.82 | 0.76 → 0.74 | -0.09 [-0.19, +0.02] | +0.06 / 0.78 → 0.84 → 0.87 | passed |
| temperance | 16 | 0.84 | 0.87 → 0.84 | +0.00 [-0.05, +0.04] | +0.04 / 0.80 → 0.80 → 0.82 | passed |
| courage | 14 | 0.89 | 0.83 → 0.84 | -0.05 [-0.13, +0.00] | +0.05 / 0.78 → 0.74 → 0.75 | unresolved (no gain over old text) |
| diligence | 16 | 0.89 | 0.93 → 0.83 | -0.05 [-0.12, -0.00] | -0.14 / 0.56 → 0.74 → 0.79 | unresolved (Jev separates slightly more) |
| wisdom | 14 | 0.89 | 0.94 → 0.92 | +0.03 [-0.01, +0.09] | -0.09 / 0.56 → 0.80 → 0.83 | passed |
| giving | 16 | 0.92 | 0.86 → 0.89 | -0.03 [-0.07, +0.02] | -0.05 / 0.66 → 0.72 → 0.69 | passed (small gain) |
| humility | 14 | 0.92 | 0.94 → 0.93 | +0.01 [-0.02, +0.08] | -0.06 / 0.70 → 0.74 → 0.82 | passed |
| justice | 15 | 0.95 | 0.93 → 0.91 | -0.04 [-0.10, -0.00] | +0.00 / 0.46 → 0.65 → 0.75 | unresolved (Jev separates slightly more) |
| property | 16 | 0.96 | 0.90 → 0.92 | -0.04 [-0.11, -0.00] | -0.01 / 0.52 → 0.73 → 0.84 | unresolved (Jev separates slightly more) |
| sexual-conduct | 4 | 0.96 | 0.98 → 0.92 | -0.05 [-0.11, +0.33] | -0.21 / 0.87 → 0.90 → 0.94 | not testable (4 stories) |
| truthfulness | 16 | 0.97 | 0.98 → 0.98 | +0.00 [-0.01, +0.03] | -0.25 / 0.18 → 0.52 → 0.50 | passed (hardest; Opus barely separates) |
