# Providers with a DPA or retention limit, priced against DeepSeek

**2026-08-23.** Research for #33, part of map #4. Every claim below comes from the provider's own
privacy, data-processing, or pricing page, fetched on 2026-08-23. No recommendation; the decision
ticket makes the call.

Two questions per provider: can it carry a POPIA s72(1)(a) transfer (a DPA, a retention limit, or
a region), and what does a Customer turn cost against DeepSeek V4.

## 1. Data handling, per provider

| Provider, model | Storage region | Retention default | Trains on API inputs | DPA | ZDR or retention exception | Needs a sales contract |
|---|---|---|---|---|---|---|
| DeepSeek V4 Flash, V4 Pro | People's Republic of China [D1] | "as long as necessary to provide our Services" [D1] | Yes, with a right to opt out [D1] | None published; PRC law governs [D2] | None | No contract exists to sign |
| Anthropic, Claude Haiku 4.5 | Workspace geo `us` is the only option; `inference_geo` is `global` by default, `us` on 4.6+ models only, so Haiku 4.5 cannot be pinned [A4] | Inputs and outputs deleted within 30 days [A2] | No: "Anthropic may not train models on Customer Content from Services" [A1] | Incorporated into the Commercial Terms by reference [A1] [A3] | ZDR by agreement, through the Sales Team; safety classifier results still retained [A5] | DPA no. ZDR yes |
| OpenAI, gpt-5-mini and gpt-5.4-mini | Region not stated for a default project; residency is per project in US, Europe (EEA + CH), UK, and seven others; in-region inference only for US and Europe [O2] | Up to 30 days for abuse monitoring, then removed [O2] [O3] | No, since 2023-03-01, unless opted in [O2] [O3] | Self-serve DPA form [O3]; DPA effective 2026-01-01 [O4] | ZDR and Modified Abuse Monitoring need prior OpenAI approval through sales; non-US residency also needs a Modified Retention amendment [O2] | DPA no. ZDR, residency yes |
| Google, Gemini Flash on Vertex AI | Customer picks the project location; `eu` multi-region endpoint keeps inference inside EU member states; `global` gives no residency [G3] [G4]. No Africa region lists a Gemini model [G4] | No at-rest retention of prompts by default; in-memory cache with a 24-hour TTL; prompts flagged by abuse classifiers kept up to 90 days in the customer's region [G2] [G5] | No: Service Specific Terms "Training Restriction" [G2] | Cloud Data Processing Addendum is incorporated into the Google Cloud agreement on acceptance [G6] | Abuse-monitoring exception by web form; Master Agreement customers exempt by default [G5] | No |
| Mistral, Small 4 and Large 3 | EU by default; US endpoint is an explicit opt-in [M2] | Output generation plus 30 rolling days for abuse monitoring [M3] | Pay-as-you-go API data not used; free mode used unless opted out [M4] [M5] | Incorporated into the agreement by reference, effective 2026-07-27 [M6] | ZDR on pay-as-you-go, by request through the Help Center, approved at Mistral's discretion [M7]; the admin docs also describe an API Privacy toggle [M5] | No |

South Africa: no primary source lists in-region inference for any of these models. Azure lists
South Africa North only for global fine-tuning, which carries no residency [Z1]. Bedrock lists
`af-south-1 (Cape Town)` for one model, Claude Opus 5, through global cross-region routing only [Z2].

## 2. List price per million tokens, USD

| Provider, model | Input | Output | Cache read | Flags |
|---|---|---|---|---|
| DeepSeek V4 Flash | 0.44 | 1.32 | 0.014 | Off-peak halves every price. Peak is 01:00 to 04:00 and 06:00 to 10:00 UTC, Monday to Friday [D3] |
| DeepSeek V4 Pro | 1.32 | 3.96 | 0.044 | Same off-peak rule [D3] |
| Claude Haiku 4.5 | 1.00 | 5.00 | 0.10 | Batch API 50% off [A6] |
| OpenAI gpt-5-mini | 0.25 | 2.00 | 0.025 | Batch and Flex 50% off [O1] |
| OpenAI gpt-5.4-mini | 0.75 | 4.50 | 0.075 | Data-residency endpoints add 10% for models released on or after 2026-03-05 [O2] |
| Gemini 3.5 Flash-Lite, global | 0.30 | 2.50 | 0.03 | Non-global endpoints add 10% [G1] |
| Gemini 3.7 Flash, global | 0.75 | 3.75 | 0.075 | **Introductory** through 2026-12-31; 1.50 / 7.50 from 2027-01-01 [G1] |
| Mistral Small 4 | 0.15 | 0.60 | 0.015 | Batch 50% off [M1] |
| Mistral Large 3 | 0.50 | 1.50 | 0.05 | Batch 50% off [M1] |

DeepSeek maps `claude-haiku*` and `claude-sonnet*` requests on its Anthropic-compatible endpoint
to `deepseek-v4-flash`, and `claude-opus*` to `deepseek-v4-pro`; `cache_control` is not supported
there [D4].

## 3. Cost per 1,000 Customer turns

Shape: one Agent call of 2,000 input and 300 output tokens per turn, plus one Judge call of 2,500
input and 100 output tokens per turn, both on the same provider. List price, no cache, no batch.
DeepSeek V4 Flash at peak is 1.0x.

| Provider, model | Agent, USD | Agent multiple | Agent + Judge, USD | Agent + Judge multiple |
|---|---|---|---|---|
| Mistral Small 4 | 0.48 | 0.38x | 0.92 | 0.36x |
| DeepSeek V4 Flash, off-peak (time-limited) | 0.64 | 0.50x | 1.25 | 0.50x |
| OpenAI gpt-5-mini | 1.10 | 0.86x | 1.93 | 0.77x |
| Gemini 3.5 Flash-Lite, global | 1.35 | 1.06x | 2.35 | 0.94x |
| DeepSeek V4 Flash, peak | 1.28 | 1.00x | 2.51 | 1.00x |
| Gemini 3.5 Flash-Lite, EU endpoint | 1.48 | 1.16x | 2.58 | 1.03x |
| Mistral Large 3 | 1.45 | 1.14x | 2.85 | 1.14x |
| Gemini 3.7 Flash, global (introductory) | 2.62 | 2.06x | 4.88 | 1.94x |
| OpenAI gpt-5.4-mini | 2.85 | 2.23x | 5.17 | 2.06x |
| Gemini 3.7 Flash, EU endpoint (introductory) | 2.89 | 2.26x | 5.36 | 2.14x |
| OpenAI gpt-5.4-mini, EU residency (+10%) | 3.13 | 2.46x | 5.69 | 2.27x |
| Claude Haiku 4.5 | 3.50 | 2.74x | 6.50 | 2.59x |
| DeepSeek V4 Pro, peak | 3.83 | 3.00x | 7.52 | 3.00x |
| Gemini 3.7 Flash, global, from 2027-01-01 | 5.25 | 4.11x | 9.75 | 3.89x |

Rows sort by Agent + Judge. Gemini 3.5 Flash-Lite global lands below DeepSeek peak on that column
and above it on Agent only, because its output price is higher and the Judge emits few tokens. The 10% OpenAI residency uplift applies
to gpt-5.4-mini only if its release date is on or after 2026-03-05, which the pricing page does
not state.

## Sources, fetched 2026-08-23

DeepSeek
- [D1] Privacy Policy, updated 2026-02-10: https://cdn.deepseek.com/policies/en-US/deepseek-privacy-policy.html
- [D2] Open Platform Terms of Service, effective 2026-04-29: https://cdn.deepseek.com/policies/en-US/deepseek-open-platform-terms-of-service.html
- [D3] Models and Pricing: https://api-docs.deepseek.com/quick_start/pricing
- [D4] Anthropic API compatibility: https://api-docs.deepseek.com/guides/anthropic_api

Anthropic
- [A1] Commercial Terms of Service, effective 2025-06-17: https://www.anthropic.com/legal/commercial-terms
- [A2] How long do you store my organization's data, updated 2026-07-01: https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data
- [A3] Data Processing Addendum, effective 2025-02-24: https://www.anthropic.com/legal/data-processing-addendum
- [A4] Data residency: https://platform.claude.com/docs/en/manage-claude/data-residency
- [A5] Zero data retention agreement, updated 2026-06-09: https://privacy.claude.com/en/articles/8956058-i-have-a-zero-retention-agreement-with-anthropic-what-products-does-it-apply-to
- [A6] Pricing: https://platform.claude.com/docs/en/about-claude/pricing

OpenAI
- [O1] Pricing: https://developers.openai.com/api/docs/pricing
- [O2] Your data: https://developers.openai.com/api/docs/guides/your-data
- [O3] Enterprise privacy, updated 2026-01-08: https://openai.com/enterprise-privacy/
- [O4] Data Processing Addendum, effective 2026-01-01: https://openai.com/policies/data-processing-addendum/

Google
- [G1] Agent Platform generative AI pricing: https://cloud.google.com/vertex-ai/generative-ai/pricing
- [G2] Zero data retention, updated 2026-08-21: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/data-governance
- [G3] Data residency, updated 2026-08-21: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/learn/data-residency
- [G4] Locations, updated 2026-08-21: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/learn/locations
- [G5] Abuse monitoring, updated 2026-08-21: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/learn/abuse-monitoring
- [G6] Cloud Data Processing Addendum: https://cloud.google.com/terms/data-processing-addendum

Mistral
- [M1] Pricing: https://docs.mistral.ai/inference/pricing and https://mistral.ai/pricing
- [M2] Where do you store my data: https://help.mistral.ai/en/articles/347629-where-do-you-store-my-data-or-my-organization-s-data
- [M3] Privacy Policy, effective 2026-07-27: https://legal.mistral.ai/terms/privacy-policy
- [M4] Do you use my data to train: https://help.mistral.ai/en/articles/347617-do-you-use-my-user-data-to-train-your-artificial-intelligence-models
- [M5] Privacy and data controls: https://docs.mistral.ai/admin/monitor-comply/privacy-data-controls
- [M6] Data Processing Addendum, effective 2026-07-27: https://legal.mistral.ai/terms/data-processing-addendum
- [M7] Can I activate ZDR: https://help.mistral.ai/en/articles/347612-can-i-activate-zero-data-retention-zdr

Regions outside the candidate list
- [Z1] Azure Foundry models sold by Azure, updated 2026-08-20: https://learn.microsoft.com/en-us/azure/ai-foundry/openai/concepts/models
- [Z2] Bedrock regional availability: https://docs.aws.amazon.com/bedrock/latest/userguide/models-region-compatibility.html
