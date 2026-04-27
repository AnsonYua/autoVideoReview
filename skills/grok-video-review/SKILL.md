---
name: grok-video-review
description: Use this skill when Codex needs to control visible Chrome for Grok web, open https://grok.com/, go to 專案, and return strict JSON only confirming success or failure.
---

# Grok Video Review

Use this skill for Grok website browser-control tasks.

Current v1 scope is intentionally minimal:

- open visible Chrome
- go to `https://grok.com/`
- navigate to `專案`
- enter the `porner director` project
- if that succeeds, return the pass JSON immediately

Do not do any other step unless the caller explicitly asks for it later.

For now, ignore any extra caller instructions about:

- video review
- video upload
- positive prompt analysis
- negative prompt analysis
- improved prompts beyond the fixed JSON shape

The browser startup sequence is part of the skill itself:

1. Open Chrome
2. Navigate to `https://grok.com/`
3. Use the existing signed-in session if available
4. Fail fast with the error JSON if Grok cannot be opened or authentication is missing

The required output is JSON only, and it must always use exactly this shape:

```json
{
  "status": "ok",
  "pass_or_fail": "PASS",
  "improved_positive_prompt": "",
  "improved_negative_prompt": "",
  "raw_text": ""
}
```

If the review cannot be completed, use the same keys and set:

```json
{
  "status": "error",
  "pass_or_fail": "FAIL",
  "improved_positive_prompt": "",
  "improved_negative_prompt": "",
  "raw_text": "error details"
}
```

## Required behavior

1. Open visible Chrome first. Do not use headless browsing.
2. Navigate to `https://grok.com/`. Prefer the existing signed-in Chrome session.
3. Go to `專案`.
4. Inside `專案`, click the `porner director` project.
5. If `porner director` is reached successfully, return:

```json
{
  "status": "ok",
  "pass_or_fail": "PASS",
  "improved_positive_prompt": "",
  "improved_negative_prompt": "",
  "raw_text": ""
}
```

6. If Grok is inaccessible, `專案` cannot be reached, `porner director` cannot be opened, or authentication is missing, return the error JSON immediately.
7. Return JSON only. Do not return prose, markdown, or code fences.

## Output rules

- `status` must be `ok` or `error`
- `pass_or_fail` must be `PASS` or `FAIL`
- `improved_positive_prompt` must always be present
- `improved_negative_prompt` must always be present
- `raw_text` must be `""` on success and may contain operational error detail on failure
- Always return exactly these 5 keys:
  - `status`
  - `pass_or_fail`
  - `improved_positive_prompt`
  - `improved_negative_prompt`
  - `raw_text`
- Do not include any extra fields
- In this v1 scope, `improved_positive_prompt` must be `""`
- In this v1 scope, `improved_negative_prompt` must be `""`

## Canonical invocation

Use this exact style when invoked from chat:

```text
Use the grok-video-review skill from /Users/hello/Desktop/grokWorkflow/skills/grok-video-review.
Review /Users/hello/Desktop/grokWorkflow/sample_data/test.mp4 against:
positive prompt: "Cinematic living room scene, slow dolly, warm light"
negative prompt: "blurry, low detail, shaky motion"
Ignore the review details for now. Open Chrome, go to https://grok.com/, then go to 專案, then open porner director.
If successful, return JSON only with status, pass_or_fail, improved_positive_prompt, improved_negative_prompt, raw_text.
```
