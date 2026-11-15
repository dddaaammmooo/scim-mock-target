"""Regenerate the screenshots in docs/images from a running MockSCIMTarget.

Read-only: it only browses (and types into the fault form without saving), so it is safe to point at
a tenant that holds real data.

    python scripts/screenshots.py [--base http://localhost:8100] [--tenant hris] [--out docs/images]

Set PLAYWRIGHT_CHANNEL=msedge (or chrome) to use an installed browser instead of Playwright's chromium.
"""
import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent


def api(base: str, path: str):
    with urllib.request.urlopen(f"{base}/api{path}", timeout=15) as r:
        return json.load(r)


def settle(page: Page, ms: int = 450):
    """Let the 220ms fade-in finish so screenshots never catch content half transparent."""
    page.wait_for_timeout(ms)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=os.environ.get("SCIM_TARGET_URL", "http://localhost:8100"))
    ap.add_argument("--tenant", default="hris")
    ap.add_argument("--out", default=str(ROOT / "docs" / "images"))
    a = ap.parse_args()
    base, tenant, out = a.base.rstrip("/"), a.tenant, Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    users = api(base, f"/tenants/{tenant}/users")
    if not users:
        print(f"tenant '{tenant}' has no users; seed it first", file=sys.stderr)
        return 1
    with_groups = [u for u in users if u.get("groups")]
    subject = (with_groups or users)[0]

    written = []
    with sync_playwright() as p:
        channel = os.environ.get("PLAYWRIGHT_CHANNEL")
        browser = p.chromium.launch(**({"channel": channel} if channel else {}))
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="dark",
                                  device_scale_factor=1)
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.set_default_timeout(15000)

        def shot(name: str, full_page: bool = False):
            settle(page)
            page.screenshot(path=str(out / f"{name}.png"), full_page=full_page)
            written.append(name)
            print(f"  wrote {name}.png")

        def open_(hash_: str):
            page.goto(f"{base}/ui/{hash_}")

        # tenants (with the x delete buttons visible)
        open_("")
        expect(page.locator(".tenant-card").first).to_be_visible()
        expect(page.locator(".tenant-del").first).to_be_visible()
        shot("tenants")

        # dashboard
        open_(f"#/t/{tenant}/dashboard")
        expect(page.get_by_role("heading", name="Dashboard")).to_be_visible()
        expect(page.locator("tbody tr").first).to_be_visible()
        shot("dashboard")

        # users list
        open_(f"#/t/{tenant}/users")
        expect(page.locator("tr.clickable").first).to_be_visible()
        shot("users")

        # user detail: summary, full JSON, request history
        open_(f"#/t/{tenant}/users/{subject['id']}")
        expect(page.get_by_role("heading", name=subject["userName"])).to_be_visible()
        expect(page.get_by_text("Request history")).to_be_visible()
        shot("user-detail")

        # groups with one expanded
        open_(f"#/t/{tenant}/groups")
        expect(page.locator("tr.clickable").first).to_be_visible()
        groups = api(base, f"/tenants/{tenant}/groups")
        biggest = max(groups, key=lambda g: len(g["members"]))["displayName"] if groups else None
        if biggest:
            page.locator("tr.clickable", has=page.get_by_text(biggest, exact=True)).first.click()
            expect(page.locator("tr.detail:not([hidden])")).to_be_visible()
        shot("groups")

        # request log, then with one entry expanded
        open_(f"#/t/{tenant}/requests")
        expect(page.locator("tr.logrow").first).to_be_visible()
        shot("request-log")
        rows = page.locator("tr.logrow")
        # prefer a write request: it has a request body as well as a response body
        for method in ("PATCH", "POST", "PUT"):
            page.locator("select").first.select_option(method)
            page.wait_for_timeout(500)
            if rows.count():
                break
        else:
            page.locator("select").first.select_option("")
            page.wait_for_timeout(500)
        expect(rows.first).to_be_visible()
        pick = rows.first
        pick.click()
        expect(page.locator("tr.detail:not([hidden])")).to_be_visible()
        pick.scroll_into_view_if_needed()
        shot("request-log-detail")

        # fault injection form (values typed but never saved)
        open_(f"#/t/{tenant}/faults")
        expect(page.get_by_role("heading", name="Fault injection")).to_be_visible()
        page.get_by_label("Added latency").fill("1500")
        page.get_by_label("Error rate").fill("15")
        page.get_by_label("Rate limit every N requests").fill("10")
        shot("faults")

        # connection details
        open_(f"#/t/{tenant}/connection")
        expect(page.locator(".copy code").first).to_be_visible()
        shot("connection")

        browser.close()
    if errors:
        print("JS errors while capturing:", *errors, sep="\n  ", file=sys.stderr)
        return 1
    print(f"{len(written)} screenshots written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
