from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Error, Page, TimeoutError, sync_playwright


SUCCESS_RESULT = {
    "status": "ok",
    "pass_or_fail": "PASS",
    "improved_positive_prompt": "",
    "improved_negative_prompt": "",
    "raw_text": "",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Drive visible Chrome to Grok and open the porner director project.")
    parser.add_argument("--video-path", default="", help="Reserved for later review/upload flow.")
    parser.add_argument("--positive-prompt", default="", help="Reserved for later review/upload flow.")
    parser.add_argument("--negative-prompt", default="", help="Reserved for later review/upload flow.")
    parser.add_argument(
        "--send-review-prompt",
        action="store_true",
        help="Type a review instruction into the Grok composer after uploading the video.",
    )
    parser.add_argument(
        "--submit-review-prompt",
        action="store_true",
        help="Click the Grok submit button after filling the review prompt.",
    )
    parser.add_argument(
        "--read-review-result",
        action="store_true",
        help="Wait for the latest assistant message and return its JSON text in raw_text.",
    )
    parser.add_argument(
        "--user-data-dir",
        default=".playwright-grok-profile",
        help="Chrome user data directory for isolated mode. Ignored when --connect-existing is used.",
    )
    parser.add_argument(
        "--connect-existing",
        action="store_true",
        help="Connect to an already-running Chrome instance over CDP instead of launching a separate profile.",
    )
    parser.add_argument(
        "--cdp-url",
        default="http://127.0.0.1:9222",
        help="Chrome DevTools endpoint used with --connect-existing.",
    )
    parser.add_argument(
        "--first-landing",
        required=True,
        help="Direct Grok project/chat URL to open before interacting with the page.",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=30000,
        help="Per-step timeout in milliseconds.",
    )
    parser.add_argument(
        "--result-timeout-ms",
        type=int,
        default=180000,
        help="Timeout for waiting for Grok's review response.",
    )
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="Keep Chrome open and wait for Enter before closing.",
    )
    return parser


def emit(result: dict[str, str]) -> None:
    print(json.dumps(result, ensure_ascii=False))


def error_result(message: str) -> dict[str, str]:
    return {
        "status": "error",
        "pass_or_fail": "FAIL",
        "improved_positive_prompt": "",
        "improved_negative_prompt": "",
        "raw_text": message,
    }


def write_debug_screenshot(page: Page, name: str) -> None:
    try:
        debug_dir = Path("tmp")
        debug_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(debug_dir / f"{name}.png"), full_page=True)
    except Exception:  # noqa: BLE001
        pass


def devtools_ready(cdp_url: str) -> tuple[bool, str]:
    probe_url = cdp_url.rstrip("/") + "/json/version"
    try:
        with urllib.request.urlopen(probe_url, timeout=3) as response:
            body = response.read().decode("utf-8")
        return True, body
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, str(exc)


def click_visible_text(page, text: str, timeout_ms: int) -> None:
    candidates = [
        page.get_by_text(text, exact=True),
        page.get_by_role("button", name=text, exact=True),
        page.get_by_role("link", name=text, exact=True),
        page.locator(f'text="{text}"'),
    ]
    last_error: Exception | None = None
    for locator in candidates:
        try:
            locator.first.wait_for(state="visible", timeout=timeout_ms)
            locator.first.click(timeout=timeout_ms)
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    if last_error is None:
        raise TimeoutError(f"Unable to find clickable text: {text}")
    raise last_error


def project_visible(page: Page, target_project: str) -> bool:
    candidates = [
        page.get_by_text(target_project, exact=True),
        page.get_by_role("button", name=target_project, exact=True),
        page.get_by_role("link", name=target_project, exact=True),
        page.locator(f'text="{target_project}"'),
    ]
    for locator in candidates:
        try:
            if locator.first.is_visible(timeout=1000):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def click_project_label_if_needed(page: Page, project_label: str, target_project: str, timeout_ms: int) -> None:
    if project_visible(page, target_project):
        return
    click_visible_text(page, project_label, timeout_ms)


def click_target_project(page: Page, target_project: str, timeout_ms: int) -> None:
    candidates = [
        page.get_by_text(target_project, exact=True),
        page.get_by_role("button", name=target_project, exact=True),
        page.get_by_role("link", name=target_project, exact=True),
        page.locator(f'text="{target_project}"'),
        page.locator(f'[aria-label="{target_project}"]'),
    ]
    last_error: Exception | None = None
    for locator in candidates:
        try:
            locator.first.wait_for(state="visible", timeout=timeout_ms)
            locator.first.click(timeout=timeout_ms)
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    if last_error is None:
        raise TimeoutError(f"Unable to open target project: {target_project}")
    raise last_error


def open_first_project_chat(page: Page, chat_section_label: str, timeout_ms: int) -> None:
    page.locator('div[dir="ltr"] a[href^="/c/"]').first.wait_for(state="attached", timeout=timeout_ms)
    result = page.evaluate(
        """(sectionLabel) => {
            const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
            const isVisible = (el) => {
                if (!el) {
                    return false;
                }
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.visibility !== 'hidden'
                    && style.display !== 'none'
                    && rect.width > 0
                    && rect.height > 0;
            };

            const elements = Array.from(document.querySelectorAll('body *'));
            const section = elements
                .filter((el) => {
                    const rect = el.getBoundingClientRect();
                    return isVisible(el)
                        && el.querySelector('a[href^="/c/"]')
                        && rect.x > 200
                        && rect.width < 500;
                })
                .map((el) => ({
                    el,
                    chatLinkCount: el.querySelectorAll('a[href^="/c/"]').length,
                }))
                .sort((left, right) => {
                    const leftRect = left.el.getBoundingClientRect();
                    const rightRect = right.el.getBoundingClientRect();
                    return right.chatLinkCount - left.chatLinkCount
                        || (leftRect.width * leftRect.height) - (rightRect.width * rightRect.height);
                })[0]?.el;

            if (!section) {
                return { clicked: false, reason: 'Unable to find the project chat list' };
            }

            const projectMatch = window.location.pathname.match(/^\\/project\\/([^/?#]+)/);
            if (!projectMatch) {
                return { clicked: false, reason: 'The current page is not a Grok project page' };
            }
            const projectId = projectMatch[1];
            const anchors = Array.from(
                section.querySelectorAll('a[href^="/c/"]'),
            )
                .map((anchor) => {
                    const anchorRect = anchor.getBoundingClientRect();
                    const row = Array.from(section.querySelectorAll('[class*="cursor-pointer"]'))
                        .find((candidate) => {
                            const rect = candidate.getBoundingClientRect();
                            return Math.abs(rect.y - anchorRect.y) < 2
                                && Math.abs(rect.height - anchorRect.height) < 2;
                        }) || anchor.parentElement || anchor;
                    const rowText = normalize(row.innerText || row.textContent);
                    const href = anchor.getAttribute('href') || '';
                    const chatMatch = href.match(/^\\/c\\/([^/?#]+)/);
                    return {
                        anchor,
                        chatId: chatMatch ? chatMatch[1] : '',
                        text: rowText,
                    };
                })
                .filter((candidate) => {
                    const text = candidate.text;
                    const anchor = candidate.anchor;
                    return isVisible(anchor)
                        && candidate.chatId
                        && text
                        && !text.includes(sectionLabel)
                        && !text.includes('附加至專案')
                        && !text.includes('Attach to project');
                })
                .sort((left, right) => (
                    left.anchor.getBoundingClientRect().y - right.anchor.getBoundingClientRect().y
                ));

            const nonEmptyChats = anchors.filter(
                (candidate) => !/^New conversation\\b/i.test(candidate.text) && !/^新增對話\\b/.test(candidate.text),
            );
            const selected = (nonEmptyChats[0] || anchors[0]);
            if (selected) {
                const target = `/project/${projectId}?chat=${selected.chatId}`;
                window.location.assign(target);
                return { clicked: true, text: selected.text, href: target };
            }

            return { clicked: false, reason: 'No existing project chat was found in the chat section' };
        }""",
        chat_section_label,
    )
    if not result.get("clicked"):
        raise TimeoutError(str(result.get("reason", "Unable to open the first project chat")))
    page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    page.wait_for_timeout(1000)


def upload_video(page: Page, video_path: str, timeout_ms: int) -> None:
    path = str(Path(video_path).expanduser().resolve())
    if not Path(path).exists():
        raise FileNotFoundError(f"Video file not found: {path}")
    file_name = Path(path).name
    existing_file_name_count = page.locator(f"text={file_name}").count()

    input_candidates = [
        page.locator('input[type="file"]'),
        page.locator('input[accept*="video"]'),
    ]
    for locator in input_candidates:
        try:
            if locator.first.count() > 0:
                locator.first.set_input_files([], timeout=timeout_ms)
                locator.first.set_input_files(path, timeout=timeout_ms)
                wait_for_video_upload_started(page, file_name, existing_file_name_count, timeout_ms)
                return
        except Exception:  # noqa: BLE001
            continue

    clip_candidates = [
        page.locator('button:has(svg)').filter(has=page.locator('svg')),
        page.get_by_role("button", name="Attach"),
        page.get_by_role("button", name="Upload"),
        page.locator('button[aria-label*="attach" i]'),
        page.locator('button[aria-label*="upload" i]'),
    ]
    last_error: Exception | None = None
    for locator in clip_candidates:
        try:
            if locator.first.is_visible(timeout=1000):
                with page.expect_file_chooser(timeout=timeout_ms) as chooser_info:
                    locator.first.click(timeout=timeout_ms)
                chooser = chooser_info.value
                chooser.set_files(path)
                wait_for_video_upload_started(page, file_name, existing_file_name_count, timeout_ms)
                return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue

    if last_error is None:
        raise TimeoutError("Unable to find a file upload control in the Grok project page")
    raise last_error


def wait_for_video_upload_started(page: Page, file_name: str, existing_file_name_count: int, timeout_ms: int) -> None:
    try:
        page.wait_for_function(
            """([fileName, previousCount]) => {
                const inputs = Array.from(document.querySelectorAll('input[type="file"]'));
                const fileInputUpdated = inputs.some((input) => input.files && input.files.length > 0);
                const currentCount = (document.body.innerText || '').split(fileName).length - 1;
                return fileInputUpdated || currentCount > previousCount;
            }""",
            arg=[file_name, existing_file_name_count],
            timeout=min(timeout_ms, 10000),
        )
    except Exception:  # noqa: BLE001
        page.wait_for_timeout(1000)


def build_review_prompt(positive_prompt: str, negative_prompt: str) -> str:
    return (
        "This video was generated from WAN 2.2.\n\n"
        f"Positive prompt:\n{positive_prompt or '(not provided)'}\n\n"
        f"Negative prompt:\n{negative_prompt or '(not provided)'}\n\n"
        "Please review the uploaded video and evaluate:\n"
        "1. Whether the video matches the positive prompt.\n"
        "2. Whether there is any abnormal physical behavior or motion.\n"
        "3. Whether the character appearance is visually abnormal or inconsistent.\n\n"
        "Return the review as JSON only in this exact shape:\n"
        '{\n'
        '  "status": "ok",\n'
        '  "pass_or_fail": "PASS",\n'
        '  "improved_positive_prompt": "",\n'
        '  "improved_negative_prompt": "",\n'
        '  "raw_text": ""\n'
        '}\n\n'
        "If the video fails, set pass_or_fail to FAIL and provide improved positive and negative prompts.\n"
        "Put your explanation inside raw_text.\n"
    )


def send_review_prompt(page: Page, prompt_text: str, timeout_ms: int) -> None:
    editor = page.locator('div.tiptap.ProseMirror[contenteditable="true"]').first
    editor.wait_for(state="visible", timeout=timeout_ms)
    editor.click(timeout=timeout_ms)
    editor.evaluate(
        """(el, text) => {
            const escapeHtml = (value) =>
                value
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;');

            const paragraphs = text
                .split(/\\n\\n+/)
                .map((part) => `<p>${escapeHtml(part).replace(/\\n/g, '<br>')}</p>`)
                .join('');

            el.innerHTML = paragraphs || '<p><br class="ProseMirror-trailingBreak"></p>';
            el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: text }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.focus();
        }""",
        prompt_text,
    )
    page.wait_for_timeout(500)


def submit_review_prompt(page: Page, timeout_ms: int) -> None:
    submit_button = page.locator('button[data-testid="chat-submit"]').first
    submit_button.wait_for(state="visible", timeout=timeout_ms)
    submit_button.wait_for(state="attached", timeout=timeout_ms)
    page.wait_for_function(
        """() => {
            const button = document.querySelector('button[data-testid="chat-submit"]');
            return !!button && !button.disabled;
        }""",
        timeout=timeout_ms,
    )
    submit_button.click(timeout=timeout_ms)


def capture_response_baseline(page: Page) -> dict[str, object]:
    assistant_messages = page.locator('[data-testid="assistant-message"]')
    count = assistant_messages.count()
    last_text = ""
    if count > 0:
        try:
            last_text = assistant_messages.last.inner_text(timeout=1000).strip()
        except Exception:  # noqa: BLE001
            last_text = ""
    return {"count": count, "last_text": last_text}


def read_review_result(page: Page, timeout_ms: int, baseline: dict[str, object] | None = None) -> dict[str, str]:
    assistant_messages = page.locator('[data-testid="assistant-message"]')
    if baseline is None:
        baseline = capture_response_baseline(page)
    wait_for_response_complete(page, timeout_ms, baseline)
    page.wait_for_timeout(1000)
    message_text = assistant_messages.last.inner_text(timeout=timeout_ms).strip()
    return normalize_result_text(message_text)


def wait_for_response_complete(page: Page, timeout_ms: int, baseline: dict[str, object]) -> None:
    previous_count = int(baseline.get("count", 0))
    previous_text = str(baseline.get("last_text", ""))
    try:
        page.wait_for_function(
            """([previousCount, previousText]) => {
                const messages = document.querySelectorAll('[data-testid="assistant-message"]');
                if (!messages.length) {
                    return false;
                }
                const last = messages[messages.length - 1];
                const text = (last.textContent || '').trim();
                if (!text || text === previousText) {
                    return false;
                }
                const hasJsonShape = text.includes('{') && text.includes('}');
                const hasNewMessage = messages.length > previousCount;
                return hasJsonShape && (hasNewMessage || text !== previousText);
            }""",
            arg=[previous_count, previous_text],
            timeout=timeout_ms,
        )
    except TimeoutError as exc:
        best_text = ""
        try:
            assistant_messages = page.locator('[data-testid="assistant-message"]')
            if assistant_messages.count() > 0:
                best_text = assistant_messages.last.inner_text(timeout=1000).strip()
        except Exception:  # noqa: BLE001
            best_text = ""
        if best_text and best_text != previous_text:
            return
        raise TimeoutError(
            f"Timed out after {timeout_ms}ms waiting for a new Grok review response. "
            "Try increasing --result-timeout-ms if Grok is still thinking."
        ) from exc


def normalize_result_text(message_text: str) -> dict[str, str]:
    parsed = parse_json_from_text(message_text)
    if parsed is not None:
        return {
            "status": str(parsed.get("status", "ok")),
            "pass_or_fail": str(parsed.get("pass_or_fail", "PASS")),
            "improved_positive_prompt": str(parsed.get("improved_positive_prompt", "")),
            "improved_negative_prompt": str(parsed.get("improved_negative_prompt", "")),
            "raw_text": str(parsed.get("raw_text", message_text)),
        }
    return {
        "status": "ok",
        "pass_or_fail": "PASS",
        "improved_positive_prompt": "",
        "improved_negative_prompt": "",
        "raw_text": message_text,
    }


def parse_json_from_text(message_text: str) -> dict[str, object] | None:
    try:
        parsed = json.loads(message_text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", message_text)
    if not match:
        return None

    try:
        parsed = json.loads(match.group(0))
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        return None
    return None


def open_target(
    page: Page,
    first_landing: str,
    timeout_ms: int,
) -> None:
    page.set_default_timeout(timeout_ms)
    page.goto(first_landing, wait_until="domcontentloaded", timeout=timeout_ms)
    wait_for_chat_history_loaded(page, timeout_ms)


def wait_for_chat_history_loaded(page: Page, timeout_ms: int) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 10000))
    except Exception:  # noqa: BLE001
        pass
    page.locator('div.tiptap.ProseMirror[contenteditable="true"]').first.wait_for(
        state="visible",
        timeout=timeout_ms,
    )
    page.locator('button[data-testid="chat-submit"]').first.wait_for(state="attached", timeout=timeout_ms)
    page.wait_for_function(
        """() => {
            const editor = document.querySelector('div.tiptap.ProseMirror[contenteditable="true"]');
            const submit = document.querySelector('button[data-testid="chat-submit"]');
            if (!editor || !submit) {
                return false;
            }
            const messages = document.querySelectorAll(
                '[data-testid="user-message"], [data-testid="assistant-message"]',
            );
            const bodyText = document.body.innerText || '';
            const hasEmptyProjectState = bodyText.includes('Start a conversation')
                || bodyText.includes('在此專案中開始對話');
            return document.readyState === 'complete' && (messages.length > 0 || hasEmptyProjectState);
        }""",
        timeout=timeout_ms,
    )
    page.wait_for_function(
        """() => new Promise((resolve) => {
            const countMessages = () => document.querySelectorAll(
                '[data-testid="user-message"], [data-testid="assistant-message"]',
            ).length;
            const initialCount = countMessages();
            const initialHeight = document.body.scrollHeight;
            window.setTimeout(() => {
                resolve(initialCount === countMessages() && initialHeight === document.body.scrollHeight);
            }, 750);
        })""",
        timeout=timeout_ms,
    )


def get_existing_page(browser: Browser) -> tuple[BrowserContext, Page]:
    if not browser.contexts:
        raise RuntimeError(
            "No existing Chrome context is available through CDP. Open at least one tab in the debug Chrome window first."
        )
    context = browser.contexts[0]
    if not context.pages:
        raise RuntimeError(
            "No existing page is available through CDP. Open a tab in the debug Chrome window first."
        )
    page = context.pages[0]
    return context, page


def main() -> int:
    args = build_parser().parse_args()
    profile_dir = Path(args.user_data_dir).expanduser().resolve()

    try:
        with sync_playwright() as playwright:
            if args.connect_existing:
                ready, detail = devtools_ready(args.cdp_url)
                if not ready:
                    emit(
                        error_result(
                            "Chrome DevTools is not reachable. Start Chrome with "
                            "`/Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome "
                            "--remote-debugging-port=9222 "
                            "--user-data-dir=/Users/hello/Desktop/grokWorkflow/.chrome-debug-profile` "
                            f"and retry. Probe detail: {detail}"
                        )
                    )
                    return 1
                browser = playwright.chromium.connect_over_cdp(args.cdp_url)
                context, page = get_existing_page(browser)
                open_target(
                    page,
                    args.first_landing,
                    args.timeout_ms,
                )
                if args.video_path:
                    upload_video(page, args.video_path, args.timeout_ms)
                if args.send_review_prompt:
                    send_review_prompt(
                        page,
                        build_review_prompt(args.positive_prompt, args.negative_prompt),
                        args.timeout_ms,
                    )
                response_baseline = capture_response_baseline(page) if args.read_review_result else None
                if args.submit_review_prompt:
                    submit_review_prompt(page, args.timeout_ms)
                if args.read_review_result:
                    emit(read_review_result(page, args.result_timeout_ms, response_baseline))
                    return 0
                if args.keep_open:
                    input("Project opened in existing Chrome. Press Enter to detach...")
                emit(SUCCESS_RESULT)
                return 0

            profile_dir.mkdir(parents=True, exist_ok=True)
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                channel="chrome",
                headless=False,
                viewport=None,
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                open_target(
                    page,
                    args.first_landing,
                    args.timeout_ms,
                )
                if args.video_path:
                    upload_video(page, args.video_path, args.timeout_ms)
                if args.send_review_prompt:
                    send_review_prompt(
                        page,
                        build_review_prompt(args.positive_prompt, args.negative_prompt),
                        args.timeout_ms,
                    )
                response_baseline = capture_response_baseline(page) if args.read_review_result else None
                if args.submit_review_prompt:
                    submit_review_prompt(page, args.timeout_ms)
                if args.read_review_result:
                    emit(read_review_result(page, args.result_timeout_ms, response_baseline))
                    return 0

                if args.keep_open:
                    input("Project opened. Press Enter to close Chrome...")

                emit(SUCCESS_RESULT)
                return 0
            finally:
                context.close()
    except (TimeoutError, Error, OSError, RuntimeError) as exc:
        try:
            write_debug_screenshot(page, "playwright_grok_review_error")
        except Exception:  # noqa: BLE001
            pass
        message = str(exc)
        if "Target page, context or browser has been closed" in message:
            message = "The browser tab or window was closed before the automation finished."
        emit(error_result(message))
        return 1


if __name__ == "__main__":
    sys.exit(main())
