# Import Project JSON

Project input uses JSON format.

Example file:

```json
{
  "title": "Sample Short Film",
  "status": "pending",
  "shots": [
    {
      "shot_number": 1,
      "script_text": "A slow establishing shot of a neon alley after rain.",
      "positive_prompt": "Cinematic neon alley, reflective ground, moody lighting, slow camera drift.",
      "negative_prompt": "blurry, low detail, shaky camera",
      "reference_image_path": "./refs/shot01-start.png",
      "status": "pending",
      "approved_iteration_id": null,
      "depends_on_previous_shot": true
    }
  ]
}
```

Required shot fields:

- `shot_number`
- `script_text`
- `positive_prompt`
- `negative_prompt`
- `reference_image_path`
- `status`
- `approved_iteration_id`
- `depends_on_previous_shot`

`reference_image_path` rules:

- First shot can set a real image path explicitly
- Later shots can also set one explicitly
- If a later shot leaves it empty, the system falls back to the previous approved shot's preview image

Import a project:

```bash
cd /Users/hello/Desktop/grokWorkflow
./start_run.sh import /Users/hello/Desktop/grokWorkflow/sample_data/project.json
```

After import, the command prints a `project_id`.

## Playwright Grok Script

There is also a direct Playwright script for the current Grok browser-control flow:

- [tools/playwright_grok_review.py](/Users/hello/Desktop/grokWorkflow/tools/playwright_grok_review.py)

Current behavior:

- opens visible Chrome
- goes to `https://grok.com/`
- opens `專案`
- opens `porner director`
- returns fixed JSON

Setup:

```bash
cd /Users/hello/Desktop/grokWorkflow
python3 -m pip install -e .
python3 -m playwright install chrome
```

Run:

```bash
cd /Users/hello/Desktop/grokWorkflow
python3 ./tools/playwright_grok_review.py --keep-open
```

Use your existing Chrome session instead of a separate profile:

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/Users/hello/Desktop/grokWorkflow/.chrome-debug-profile
```

Then run:

```bash
cd /Users/hello/Desktop/grokWorkflow
python3 ./tools/playwright_grok_review.py --connect-existing --keep-open
```

Quick check before attaching:

```bash
curl http://127.0.0.1:9222/json/version
```

If that does not return JSON, Chrome is not exposing DevTools and Python cannot attach to the current browser.

## Repo Skill

This repo now includes a local Codex skill for Grok web video review:

- [skills/grok-video-review/SKILL.md](/Users/hello/Desktop/grokWorkflow/skills/grok-video-review/SKILL.md)

Use it for direct Codex chat-style review with a local video path, positive prompt, and negative prompt. The skill is designed to use Chrome browser control against Grok web and return JSON only.

Canonical prompt:

```text
Use the grok-video-review skill from /Users/hello/Desktop/grokWorkflow/skills/grok-video-review.
Review /Users/hello/Desktop/grokWorkflow/sample_data/test.mp4 against:
positive prompt: "Cinematic living room scene, slow dolly, warm light"
negative prompt: "blurry, low detail, shaky motion"
Return JSON only with status, pass_or_fail, improved_positive_prompt, improved_negative_prompt, raw_text.
```
