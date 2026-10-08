"""Exercise the real workbench and HTTP API with Chromium; no mock responses.

Run with the pii extra and ``pip install playwright; playwright install chromium``.
Screenshots go to --output; all document/key fixtures use a private temp directory.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import threading

from ganglion.console.server import create_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("runs/ui-preview"))
    parser.add_argument("--native", action="store_true", help="also exercise the trained Qwen checkpoint")
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright, expect
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ganglion-browser-") as temp:
        fixture = Path(temp)
        server = create_server(base_dir=fixture / "runs" / "traces", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 1100}, accept_downloads=True)
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.add_init_script("window.showSaveFilePicker = undefined")
                page.goto(server.url)
                expect(page.locator("#connection")).to_have_text("API 연결됨")
                page.screenshot(path=str(args.output / "workbench-desktop.png"), full_page=True)
                original = "이름: 김민수\r\n주소: 서울특별시 강남구 테헤란로 123 4층\r\n전화: 010-1234-5678\r\n이메일: alice@example.com\r\n기존 토큰 <PERSON_1>과 이모지 🌱를 보존합니다.\r\n"
                page.locator("#field-document").fill(original)
                with page.expect_download() as event:
                    page.get_by_role("button", name="키 생성 & 개인키 저장").click()
                key_file = fixture / "key.json"
                event.value.save_as(key_file)
                page.get_by_role("button", name="실행 시작").click()
                expect(page.locator("#status")).to_have_text("완료", timeout=30000)
                expect(page.locator("#artifacts")).to_contain_text("recovery.bin")
                artifact_paths = {}
                for name in ("document.txt", "recovery.bin"):
                    row = page.locator(".artifact").filter(has=page.locator("span", has_text=name))
                    with page.expect_download() as event:
                        row.get_by_role("button", name="저장 ↓").click()
                    artifact_paths[name] = fixture / name
                    event.value.save_as(artifact_paths[name])
                masked = artifact_paths["document.txt"].read_text()
                assert "김민수" not in masked and "alice@example.com" not in masked
                page.get_by_role("button", name="피드백 저장").click()
                expect(page.locator("#feedback-status")).to_contain_text("저장했습니다")
                analysis = json.loads(page.locator("#feedback-analysis-json").text_content())
                assert analysis["status"] == "unclassified" and analysis["final"]["f1"] is None
                normalized = original.replace("\r\n", "\n")
                gold = []
                for literal, kind in (("김민수", "PERSON"), ("서울특별시 강남구 테헤란로 123 4층", "ADDRESS"),
                                      ("010-1234-5678", "PHONE"), ("alice@example.com", "EMAIL")):
                    start = normalized.index(literal)
                    gold.append({"start": len(normalized[:start].encode()),
                                 "end": len(normalized[:start + len(literal)].encode()), "type": kind})
                page.get_by_text("정답 구간 추가 (UTF-8 바이트)", exact=True).click()
                page.locator("#expected-spans").fill(json.dumps(gold))
                page.get_by_role("button", name="피드백 저장").click()
                expect(page.locator("#feedback-status")).to_contain_text("입력한 정답 구간에 대한 오류")
                analysis = json.loads(page.locator("#feedback-analysis-json").text_content())
                assert analysis["status"] == "partial" and analysis["final"]["f1"] is None
                page.locator("#gold-complete").check()
                page.get_by_role("button", name="피드백 저장").click()
                expect(page.locator("#feedback-status")).to_contain_text("F1 100.00%")
                analysis = json.loads(page.locator("#feedback-analysis-json").text_content())
                assert analysis["status"] == "pass" and analysis["final"]["f1"] == 1
                page.screenshot(path=str(args.output / "workbench-result.png"), full_page=True)
                page.locator('[data-view="restore"]').click()
                page.locator("#restore-document").set_input_files(artifact_paths["document.txt"])
                page.locator("#restore-recovery").set_input_files(artifact_paths["recovery.bin"])
                page.locator("#restore-key").set_input_files(key_file)
                page.locator("#restore-button").click()
                expect(page.locator("#restore-result")).to_contain_text("원문 바이트 검증을 통과했습니다", timeout=30000)
                with page.expect_download() as event:
                    page.get_by_role("button", name="복원 문서 저장").click()
                restored = fixture / "restored.txt"
                event.value.save_as(restored)
                # A textarea normalizes CRLF according to browser HTML semantics.
                assert restored.read_bytes() == original.replace("\r\n", "\n").encode()
                page.locator('[data-view="workspace"]').click()
                page.locator("#program").select_option("tool-planner")
                page.locator("#field-prompt").fill("거실 불 켜줘")
                page.locator("#run-button").click()
                expect(page.locator("#status")).to_have_text("완료", timeout=30000)
                expect(page.locator("#result-json")).to_contain_text('"plan"')
                expect(page.locator("#artifacts")).to_contain_text("plan.json")
                expect(page.locator("#feedback-gold")).to_be_hidden()
                page.locator('[data-view="specs"]').click()
                spec = json.loads(page.locator("#spec-json").input_value())
                spec.update(id="browser-custom", title="사용자 프로그램")
                page.locator("#spec-json").fill(json.dumps(spec, ensure_ascii=False))
                page.locator("#register-spec").click()
                expect(page.locator("#spec-list")).to_contain_text("사용자 프로그램")
                page.locator('[data-view="history"]').click()
                expect(page.locator("#jobs")).to_contain_text("도구 호출 계획")
                if args.native:
                    page.locator('[data-view="workspace"]').click()
                    page.locator("#program").select_option("pii-qwen")
                    source = fixture / "native-source.txt"
                    source.write_bytes(original.encode())
                    page.locator("#field-document-file").set_input_files(source)
                    page.locator("#field-public_key").fill(json.loads(key_file.read_text())["public_key"])
                    page.locator("#run-button").click()
                    expect(page.locator("#status")).to_have_text("완료", timeout=120000)
                    expect(page.locator("#result-json")).to_contain_text('"output_tokens": 0')
                    expect(page.locator("#result-json")).to_contain_text('"qwen_native"')
                page.locator('[data-view="workspace"]').click()
                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path=str(args.output / "workbench-mobile.png"), full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                assert page.evaluate("Object.keys(localStorage).length") == 0
                assert not errors, errors
                browser.close()
            print(json.dumps({"status": "passed", "flows": ["keygen", "pii-run", "downloads", "restore", "feedback", "tool-plan", "spec-register", "history", "mobile-layout"], "native": args.native, "screenshots": str(args.output)}))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
