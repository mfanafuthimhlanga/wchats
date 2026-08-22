# Judge biases when the Judge and the Agent share a provider

What the primary literature documents about LLM-as-a-judge biases, how each one is measured, and
which ones the W Chats Harness must measure now that every pinned model resolves to one DeepSeek
model. Feeds the `ship` definition (MASTERPLAN M2) and BACKLOG `8.7 · judge-bias-probes`.

## The routing fact the table rests on

DeepSeek's Anthropic-compatible endpoint (`https://api.deepseek.com/anthropic`) maps every
`claude-haiku-*` and `claude-sonnet-*` name to `deepseek-v4-flash` and every `claude-opus-*` name to
`deepseek-v4-pro` (api-docs.deepseek.com/guides/anthropic_api). The repo pins only haiku and sonnet
names: the customer agent (`config.py:240`), the eval judge (`eval_service.py:101`), the scenario
generator that writes reference answers (`scenario_service.py:19`), the Ragas judge
(`retrieval_eval.py:58`), the validation and metadata judges, the red-team attackers
(`red_team_service.py:51-52`) and the deployment orchestrator (`deployment_service.py:37`). One set
of weights therefore writes the answer, writes the reference it is graded against, and grades it.

## The table

| Bias | Evidence (paper, year, finding) | Measurement the Harness runs | Applies with a shared provider |
|---|---|---|---|
| Self-preference (self-enhancement) | Zheng et al. 2023, arXiv 2306.05685: GPT-4 wins 10% more and Claude-v1 25% more under its own judging, but the authors state the data cannot settle whether self-enhancement exists. Panickssery, Bowman, Feng 2024, arXiv 2404.13076: self-preference is scoring one's own output higher than others' while humans rate them equal; GPT-4 recognises its own summaries 73.5% of the time out of the box, and fine-tuning self-recognition up raises self-preference linearly. Wataoka et al. 2024, arXiv 2410.21819: bias = P(judge prefers \| self, human prefers) minus P(judge prefers \| other, human prefers); GPT-4 scores about 0.52 on Chatbot Arena data. arXiv 2604.22891 (2026): equal-quality pairs with no human labels, SPB = own-pick rate minus the same judge's pick rate on third-party pairs; DeepSeek-V3.2 0.226 (second highest of 20 models), DeepSeek-V3-0324 0.024, DeepSeek-R1-0528 -0.097, Claude-Sonnet-4.5 -0.229. No V4 model measured anywhere. | Build equal-quality pairs: one response from the agent (deepseek-v4-flash), one from a foreign model on a different provider, matched so neither side is better on the rubric. Judge both orderings. Report SPB = share of pairs where the judge picks the agent's answer minus the judge's pick rate for a pre-designated side on foreign-versus-foreign pairs. For the pointwise judges the repo runs today, score the same pair set on the 1 to 5 rubric and report the mean score gap, with a human-labelled subset as the anchor (Panickssery's individual setting). Gate: SPB within the noise band of the null pairs. | Yes. This is the bias the shared provider creates. The DeepSeek-V4 report (arXiv 2606.19348, section 5.1.1) trains the policy as its own Generative Reward Model, so judging its own style is the training objective. |
| Reference leakage (preference leakage) | Li et al. 2025, arXiv 2502.01534: a judge favours a student trained on its own synthetic data; Preference Leakage Score for the same-model case 23.6%, inheritance 19.3 to 22.3%, same family 8.9%, unrelated 2.8%; subjective tasks leak most, maths least. Ant Group 2025, arXiv 2506.22316: names reference-answer score bias in absolute scoring and finds a full-mark reference the safest choice when one is supplied. Neither measures a reference written by the judge itself. | Score one fixed set of agent responses twice: against the DeepSeek-written `reference_answer` rows and against the owner-authored rows (`label_trust_tier`, BACKLOG `4.12`). Report the pass-rate delta per metric. Second cut: rescore a sample with a foreign judge on a different provider and report the delta in `pass_rates`. Gate: both deltas inside the confidence interval of the run. | Yes. The scenario generator, the judge and the agent are the same weights, so every mined reference is the same-model case Li et al. measure at 23.6%. |
| Familiarity (low perplexity) | Wataoka et al. 2024, arXiv 2410.21819: judges rate lower-perplexity text higher than humans do regardless of who wrote it, which is the mechanism behind self-preference. | Paraphrase a scored agent answer with a foreign model, holding the facts fixed, and rescore. Report the verdict change rate. Needs no logprobs. | Yes. The agent's output is the lowest-perplexity text the judge can see. |
| Length (verbosity) | Zheng et al. 2023: the repetitive-list attack fools Claude-v1 and GPT-3.5 91.3% of the time, GPT-4 8.7%. Dubois et al. 2024, arXiv 2404.04475: a logistic regression with a length term yields a length-controlled win rate; Spearman with Chatbot Arena rises from 0.94 to 0.98, and a verbose prompt moves the baseline from 22.9% to 64.3% uncontrolled versus 41.9% to 51.6% controlled. Ye et al. 2024, arXiv 2410.02736 (CALM): verbosity Robustness Rate, GPT-4o 0.977. | Pad a correct answer with true but irrelevant sentences, and separately duplicate its list items; rescore. Robustness Rate = share of verdicts unchanged. For any pairwise use, fit the length-controlled regression before reporting a win rate. Gate: RR at or above the judge's own repeat-consistency rate. | Applies with any provider. The shared provider sharpens it because the agent's length distribution is the one the judge was trained to reward. |
| Position | Zheng et al. 2023: GPT-4 gives the same verdict after a swap 65.0% of the time, Claude-v1 23.8%. Wang et al. 2023, arXiv 2305.17926: conflict rate after swapping, ChatGPT 82.5% and 52.5% in its two settings, GPT-4 46.3% and 5.0%; Balanced Position Calibration averages both orderings. Shi et al. 2024, arXiv 2406.07791: Position Consistency, Repetition Stability, Preference Fairness over 150,000 judgements; GPT-4 PC about 0.82. arXiv 2504.09946 (2025): DeepSeek-R1 prefers the later option (Emerton-DPO accuracy 60% when the correct answer is first, 85% when second), DeepSeek-V3 shows the same pattern. DeepSeek-GRM (arXiv 2504.02495) shuffles responses "to avoid positional biases". | Judge every pair in both orders. Report Position Consistency and the conflict rate; resolve conflicts with BPC. Gate: PC at or above Repetition Stability on the same pairs. | Applies with any provider. Dormant while the judges score single responses; live the first time two prompts are compared through a judge. |
| Injected cues (authority, bandwagon, distraction, sentiment, formatting, fake citation, superficial reflection) | Chen et al. 2024, arXiv 2402.10669: Attack Success Rate; GPT-4 shifts toward a fake citation 0.66, rich formatting 0.32, a planted factual error 0.09. Koo et al. 2023, arXiv 2309.17012 (CoBBLEr): order, compassion fade, egocentric, salience, bandwagon and attentional biases on 15 LLMs, roughly 40% of comparisons biased. CALM 2024: twelve bias types with Robustness Rate and Consistency Rate. arXiv 2504.09946: DeepSeek-R1 bandwagon drops Emerton accuracy 73% to 37%, authority moves it 68% to 81%, and inserting "wait, let me think" shifts verdicts (superficial reflection bias). | Perturb one response per cue with content held correct, rescore, report ASR per cue. Run the reflection-phrase cue because `deepseek-v4-flash` answers in thinking mode by default (api-docs.deepseek.com/quick_start/pricing). Gate: ASR per cue below the judge's repeat-inconsistency rate. | Applies with any provider. The red-team attackers share the provider too, so an attacker can learn the cues its own judge rewards. |
| Repeat inconsistency (the noise floor) | Shi et al. 2024: Repetition Stability. CALM 2024: Consistency Rate, share of identical inputs that receive the same verdict. | Score the same input three times, report the share with identical verdicts. Every gate above is measured against this floor, so it runs first. | Applies with any provider. |

## What the DeepSeek primary sources say

- The V4 technical report (arXiv 2606.19348, section 5.1.1): "the actor network natively functions
  as the GRM, enabling the joint optimization of the model's evaluative (judging) proficiency
  alongside its standard generative capabilities." It reports no self-preference, position or
  length measurement. Its open-ended evaluations use blind human annotators (section 5.4).
- The V4 Flash and Pro model cards (huggingface.co/deepseek-ai) mention no judge use and no bias.
- DeepSeek-GRM (arXiv 2504.02495) shuffles candidate responses before sampling to avoid position
  bias and reports "domain biases" across reward benchmarks; it does not measure self-preference.
- The only published DeepSeek judge numbers are for V3, V3.2 and R1 (arXiv 2604.22891, arXiv
  2504.09946). Nothing measures deepseek-v4-flash or deepseek-v4-pro as a judge.

## What it means for `ship`

- `pass_rates` is `AVG(score)` from a judge grading its own generations against its own references.
  Under the published same-model leakage figure, a 0.85 bar can be met by familiarity alone.
- The measurement `ship` cannot skip is the foreign-judge delta: rescore a sample of the run's
  agent responses with a judge on a different provider, or against owner-authored references, and
  require the pass-rate delta inside the run's confidence interval. Verbosity and position probes
  cannot see this bias, because padding and swapping change the text, not its author.
- Every probe reports against the repeat-inconsistency floor; a probe that "passes" below the floor
  has measured noise.
