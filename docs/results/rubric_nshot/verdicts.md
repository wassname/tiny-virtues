# Who was right? Supervisor verdicts on the disagreement stories

Written by PI/Claude-opus (Opus 5.5, supervisor), 2026-10-22. Scores are signed −1..+1 (negative = conflict with the virtue). "Jev" = rubric v2 with Opus-labelled story examples (loop 2; wisdom loop 3). Each group lists the 3 stories the report selected by largest Jev-vs-Opus residual disagreement (`outputs/rubric_gym/nshot-all-groups/loop-2/cards/<group>.md`; wisdom `loop-3`), read in full by the supervisor: passage, selected act, every virtue's Opus and Jev score and Opus's reason, and where noted a DeepSeek reasoning replay (`loop-1/replays/`). Verdicts compare both judges with the rubric v2 source quotations (`meanings.md`). They are one reader's judgement, not ground truth.

Verdict words: **Opus right**, **Jev right**, **both defensible**, **agree** (same direction, size differs), **both wrong**.

## Overall judgement

The new rubrics do what the goal asks in most groups: Jev's differences between traditions move to where Opus's are (same-pattern r rises from old text in 12 of 13 groups, `table.md`), and the ordinary-word control fails everywhere. Of the 39 stories read (counting each story's flagged virtue), Jev is right in 6, both are defensible in 4, they agree in 14, Opus is right in 14, and both are wrong in 1. These are the worst-disagreement stories, selected to find errors, so they do not estimate an error rate. Jev's errors fall into four classes: (1) crediting "resists this vice" when the vice is not in play (reduced by a third in loop 4, not gone); (2) missing tradition-specific world knowledge (the Islamic Law on wine, the archaic Legge line); (3) not inferring motive (a "gift" that is a bribe); (4) over-strong scores, which make Jev separate traditions somewhat more than Opus (pair-r gaps in patience, diligence, justice and property just exclude 0). Courage is the one group where rubric v2 did not beat the old text. Sexual conduct has only 4 stories, so it is not testable.

## truthfulness: meanings differ (speech vs certainty vs promise-keeping)
Hindu "relating facts as they are" is about reporting; Lefin's Truth forbids saying anything one is not certain of; Confucian good faith is "governed by right" and is not keeping a promise at all costs; Islamic 33:35 gives no detail.
- rei-0074, a rationalist swears never to break the confessional seal even to save humanity: Confucian sincerity Opus −.35, Jev −.17; the other four Opus about +.23, Jev −.29..+.12. **Opus right**: only the Confucian limit says good faith yields to right, so Confucian should be the outlier; Jev puts Hindu lowest without textual reason.
- ana-186, Tsz-lu "never let a night pass between promise and performance": Confucian highest in both (Opus +.52, Jev +.86), Hindu lower in both (+.28/+.29). **Agree**; Jev amplifies.
- jtk-1839, Candadevi's Act of Truth: all positive in both; Jewish Truth Opus +.36, Jev +.24. **Agree**.
Finding: distinction partly supported; Opus barely separates the traditions here (pair r .97), so there is little to match.

## anger: meanings differ (feeling arises / restrained / revenge / speech manner / ill-will)
See [anger meanings](meanings.md#anger).
- hse-0142, Arjuna boasts he will consume all foes: Buddhist no-malevolence Opus −.35, Jev −.85. **Both defensible**; MN41 names the wish that beings be slain, Jev is stronger.
- jrm-0132, God blesses Adam and Eve's marriage: Buddhist no-malevolence Opus +.35, Jev +.79. **Opus right**: the blessing's words are not given, so the well-wish is inferred; Jev over-credits.
- ach-0118, "a clever tongue ... ends up detested": Jewish Calmness Opus −.47, Jev −.66. **Agree** (Lefin: avoid arousing hatred).
Finding: distinction supported.

## sexual conduct: meanings partly differ (thought vs bodily act)
Christian and Hindu chastity cover thought; the Buddhist precept concerns illicit sexual relations.
- gos-015, "whosoever looketh on a woman to lust": Christian/Hindu/Islamic about −.9 in both; Buddhist precept Opus −.25, Jev −.16. **Agree**, and the source-supported distinction survives.
- thc-0558, Ratthapala ordains: all positive in both. **Agree**.
- jrm-0148, maxim scorning celibacy: all mildly positive in both. **Agree** (weak case).
Finding: distinction supported but untestable statistically (4 stories).

## humility: meanings differ (rationalist error-anticipation vs self-abasement)
- des-087, hermit refuses money to avoid vanity: all positive. **Agree**.
- stoic:pla-008, Fundanus studies anger in others to cure his own: rationalist humility Opus +.32, Jev +.60. **Jev right**: "specific actions in anticipation of your own errors" fits exactly.
- ana-064, Mang Chi-fan, praised as "no sounder of his own praises": Opus about +.3, Jev about −.5 on all five, in every condition. **Opus right** (DeepSeek replays agree with Opus); Jev misreads Legge's archaic line.
Finding: distinction supported; one Jev reading error.

## patience: meanings differ (Islamic patience includes constancy in right endeavour)
- isg-0037 dervish squanders a gift; hnm-0870 man sells his mother; thc-0187 a slothful, rough-tongued monk: Jev Islamic patience about −.7..−.8, others near 0; Opus the same direction, milder. **Jev right in direction** (Yusuf Ali: "patience and constancy, in suffering and in right endeavour"), over-strong.
Finding: distinction supported by source; Jev separates it more than Opus (pair-r gap −.08 [−.17, −.00]).

## justice: meanings differ (Stoic just deserts vs Islamic al-munkar vs Hindu appropriation)
- des-029, Macarius helps the robber load his horse: Stoic justice Jev −.67, Stoic injustice resisted Jev +.58; Opus near 0 on both. **Both defensible**: he did no wrong himself but abetted one; Jev splits this coherently.
- cas-1796, the believers share all property: Islamic injustice resisted Opus +.20, Jev +.79. **Opus right**: vice not in play (class 1).
- stoic:cic-030, sick man reuses a remedy he promised not to use: Stoic justice Opus +.12, Jev −.32. **Opus right**: Cicero's own exception lets such promises yield.
Finding: unresolved; pattern much improved (.46→.75), Jev separates slightly more than Opus.

## property: meanings differ (Christian restitution/usury vs Taoist housebreaking)
- xuh-0379, Lord Xinling steals the king's tally to save the state: Taoist housebreaking Opus −.27, Jev +.06. **Jev right**: no house was broken into (Opus itself calls the line "only loosely applicable").
- des-024, Anastasius forgives the thief of his book: Taoist housebreaking resisted Opus +.06, Jev +.61. **Opus right** (class 1).
- aqs-0001, Aquinas: taking in urgent necessity: Christian theft resisted Opus +.03, Jev +.36. **Both defensible**: the source text permits it; Jev's "resists" overstates (class 1).
Finding: unresolved; pattern much improved (.52→.84), Jev separates slightly more than Opus.

## giving: meanings differ (Cicero's harmful-gift limit, Taoist no-regret, Buddhist relinquishment)
- zzh-2073, Yung-tsze bribes a judge with his daughter: Christian covetousness Opus −.68, Jev −.11 even with balanced examples. **Opus right** (class 3: motive).
- rnv-0178, donating to wasteful charities: Stoic charity negative in both (Cicero: proportion); Buddhist giving Opus mild, Jev −.40. **Opus right** on Buddhist (waste lowers quality, not the opposite of giving); Stoic distinction shared.
- stoic:ben-042, refusing money that would fund a mistress: Stoic charity positive in both; Taoist "do not begrudge" Opus neutral, Jev −.62. **Opus right**: a prudent refusal is not begrudging.
Finding: passed on the pair-r test; pattern only .66→.69.

## compassion: meanings differ (Hindu non-killing is universal; Taoist compassion joined to battle)
- lsc-0219, Viscount Jian kills his beloved mule to save a subject: Hindu non-killing strongly negative in both (Opus −.73, Jev −.98). **Agree**. Buddhist compassion Opus +.05, Jev +.51: **Opus right** (MN 7 compassion is boundless, the mule counts).
- des-023, old man forgives the thieving brother: all positive in both. **Agree**.
- jtk-4298, Ajatasattu builds Devadatta a monastery: all mildly negative; Christian brotherly love Opus −.09, Jev +.05. **Opus right** (minor).
Finding: passed.

## courage: meanings differ (Stoic endurance; Jewish might = self-conquest; Buddhist energy for others' welfare)
- zhu-091, Robber Chê's speech on the "virtues" of thieves: Buddhist Energy Opus −.4, Jev −.78. **Both defensible** (DeepSeek agrees with Jev: energy must be for others' welfare); Opus is inconsistent (neutral on cowardice, negative on every courage item).
- isg-0012, a prince rallies fleeing troops: Stoic courage Opus +.5, Jev +.19, yet Jev's Stoic cowardice-resisted is +.71. **Opus right**: Jev over-applies "physical daring alone does not establish courage" and contradicts itself within the Stoic list.
- stoic:pig-115, an employee intervenes against a harassing boss: Jewish "mighty" Opus +.37, Jev +.12. **Jev right**: Ben Zoma's might is subduing one's own inclination, not facing others.
Finding: unresolved; rubric v2 did not beat the old text (.78→.75).

## diligence: meanings differ (Christian fervour in God's service; Luzzatto's zeal for mitzvot)
- eph-0143, Sergius burns the synagogue: Jewish zeal both strongly negative (source-supported). Christian sloth resisted Opus +.21, Jev +.70. **Opus right** on sloth (class 1).
- hse-0171, Kaushika leaves his parents to study the Vedas: Christian diligence Opus −.06, Jev −.66. **Opus right**: study fits Gibson's fervour; Jev follows the fowler's rebuke.
- rnv-0082, procrastination "tempted by mere inertia or laziness": strongly negative in both. **Agree**.
Finding: unresolved; pattern improved (.56→.79), Jev separates slightly more than Opus.

## temperance: meanings differ (Islamic appetite under the Law; Christian food and drink only)
- stoic:dl-027, Chrysippus quiet but tipsy at wine parties: Islamic temperance Opus −.37, Jev +.30. **Opus right** (class 2: the Law forbids wine).
- des-068, a monk refuses cooked food to boast: Christian gluttony resisted Opus +.24, Jev +.61. **Opus right**: refusal is in play, but the motive is pride.
- des-078, a monk scorns brethren who eat with guests in a fast: Hindu self-control Opus −.18, Jev −.44. **Agree**; Jev stronger.
Finding: passed.

## wisdom: meanings differ (Confucian wisdom = knowing and keeping family duties)
- ach-0027, "observe the rites in serving your parents": Confucian wisdom Opus +.60, Jev +.87, others low in both. **Agree**; matches Mencius 4A27.
- dwa-0167, Sarah asks only for strength against lust: same ordering in both (Jewish watchfulness, Christian prudence highest). **Agree**.
- aes-055, the groom steals the horse's oats: every wisdom virtue negative in both (Opus about −.5, Jev about −.85). **Both wrong**: theft is not shown to be a failure of knowledge for most of these definitions; a shared good/bad spill.
Finding: passed (.56→.83).
