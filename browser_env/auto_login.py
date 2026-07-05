"""Script to automatically login each website"""
import argparse
import glob
import os
import time
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
from pathlib import Path

from playwright.sync_api import sync_playwright

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from browser_env.env_config import (
    ACCOUNTS,
    AGILEFANT,
    FOURGABOARDS,
    GADAEL,
    GITLAB,
    REDDIT,
    ONESTOPSHOP,
    SHOPPING,
    SHOPPING_ADMIN,
    TIMEOFF,
    KEYSTONEJS,
    NODEBB,
    PARABANK,
    POSTMILL,
    REALWORLD,
)

HEADLESS = True
SLOW_MO = 0


SITES = [
    "gitlab",
    "onestopshop",
    "shopping",
    "shopping_admin",
    "reddit",
    "timeoff",
    "keystonejs",
    "nodebb",
    "postmill",
    "gadael",
    "parabank",
    "realworld",
    "agilefant",
    "4gaboards",
]
URLS = [
    f"{GITLAB}/-/profile",
    f"{ONESTOPSHOP}/wishlist/",
    f"{SHOPPING}/wishlist/",
    f"{SHOPPING_ADMIN}/dashboard",
    f"{REDDIT}/user/{ACCOUNTS['reddit']['username']}/account",
    f"{TIMEOFF}/",
    f"{KEYSTONEJS}/keystone",
    f"{NODEBB}/login",
    f"{POSTMILL}/user/{ACCOUNTS['postmill']['username']}",
    f"{GADAEL}/#/home",
    f"{PARABANK}/overview.htm",
    f"{REALWORLD}/",
    f"{AGILEFANT}/dailyWork.action",
    f"{FOURGABOARDS}/",
]
EXACT_MATCH = [True, True, True, True, True, False, False, False, False, False, False, False, False, False]
KEYWORDS = [
    "",
    "",
    "",
    "Dashboard",
    "Delete",
    "",
    "",
    "",
    ACCOUNTS["postmill"]["username"],
    "test user",
    "Account Services",
    "@Heath93",
    "The daily work of",
    "Dashboard",
]


def login_postmill(page) -> None:
    username = ACCOUNTS["postmill"]["username"]
    password = ACCOUNTS["postmill"]["password"]
    page.goto(f"{POSTMILL}/login")
    page.wait_for_timeout(1000)

    username_selectors = [
        'input[name="username"]',
        'input[name="_username"]',
        'input[name="email"]',
        'input[type="text"]',
    ]
    password_selectors = [
        'input[name="password"]',
        'input[name="_password"]',
        'input[type="password"]',
    ]

    for selector in username_selectors:
        if page.locator(selector).count():
            page.fill(selector, username)
            break
    else:
        raise RuntimeError("Could not find Postmill username field")

    for selector in password_selectors:
        if page.locator(selector).count():
            page.fill(selector, password)
            break
    else:
        raise RuntimeError("Could not find Postmill password field")

    page.click('button[type="submit"], input[type="submit"]')
    page.wait_for_timeout(2000)


def login_gadael(page) -> None:
    username = ACCOUNTS["gadael"]["username"]
    password = ACCOUNTS["gadael"]["password"]
    page.goto(f"{GADAEL}/#/login")
    page.wait_for_timeout(1000)
    page.locator("input").nth(0).fill(username)
    page.locator("input").nth(1).fill(password)
    page.get_by_role("button", name="Sign In").click()
    page.wait_for_timeout(2000)


def login_parabank(page) -> None:
    username = ACCOUNTS["parabank"]["username"]
    password = ACCOUNTS["parabank"]["password"]
    page.goto(PARABANK)
    page.wait_for_timeout(1000)
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    page.click('input[value="Log In"], button:has-text("Log In")')
    page.wait_for_timeout(2000)


def login_realworld(page) -> None:
    username = ACCOUNTS["realworld"]["username"]
    password = ACCOUNTS["realworld"]["password"]
    page.goto(f"{REALWORLD}/signin")
    page.wait_for_timeout(1000)
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    page.click('button:has-text("Sign In")')
    page.wait_for_timeout(2000)


def login_agilefant(page) -> None:
    username = ACCOUNTS["agilefant"]["username"]
    password = ACCOUNTS["agilefant"]["password"]
    page.goto(f"{AGILEFANT}/login.jsp")
    page.wait_for_timeout(1000)
    page.fill('input[name="j_username"]', username)
    page.fill('input[name="j_password"]', password)
    page.click('input[type="submit"][value="Log in"]')
    page.wait_for_timeout(3000)


def login_4gaboards(page) -> None:
    username = ACCOUNTS["4gaboards"]["username"]
    password = ACCOUNTS["4gaboards"]["password"]
    page.goto(f"{FOURGABOARDS}/login")
    page.wait_for_timeout(1000)
    page.fill('input[name="emailOrUsername"]', username)
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"], button:has-text("登录"), button:has-text("Log in")')
    page.wait_for_timeout(3000)


def is_expired(
    storage_state: Path, url: str, keyword: str, url_exact: bool = True
) -> bool:
    """Test whether the cookie is expired"""
    if not storage_state.exists():
        return True

    context_manager = sync_playwright()
    playwright = context_manager.__enter__()
    try:
        browser = playwright.chromium.launch(headless=True, slow_mo=SLOW_MO)
        context = browser.new_context(storage_state=storage_state)
        page = context.new_page()
        page.goto(url)
        time.sleep(1)
        d_url = page.url
        content = page.content()
    finally:
        context_manager.__exit__(None, None, None)
    
    if keyword:
        return keyword not in content
    else:
        if url_exact:
            return d_url != url
        else:
            return url not in d_url


def renew_comb(comb: list[str], auth_folder: str = "./.auth") -> None:
    os.makedirs(auth_folder, exist_ok=True)
    for c in comb:
        context_manager = sync_playwright()
        playwright = context_manager.__enter__()
        try:
            browser = playwright.chromium.launch(headless=HEADLESS)
            context = browser.new_context()
            page = context.new_page()

            if c == "shopping":
                username = ACCOUNTS["shopping"]["username"]
                password = ACCOUNTS["shopping"]["password"]
                page.goto(f"{SHOPPING}/customer/account/login/")
                page.get_by_label("Email", exact=True).fill(username)
                page.get_by_label("Password", exact=True).fill(password)
                page.get_by_role("button", name="Sign In").click()

            if c == "onestopshop":
                username = ACCOUNTS["onestopshop"]["username"]
                password = ACCOUNTS["onestopshop"]["password"]
                page.goto(f"{ONESTOPSHOP}/customer/account/login/")
                page.get_by_label("Email", exact=True).fill(username)
                page.get_by_label("Password", exact=True).fill(password)
                page.get_by_role("button", name="Sign In").click()

            if c == "reddit":
                username = ACCOUNTS["reddit"]["username"]
                password = ACCOUNTS["reddit"]["password"]
                page.goto(f"{REDDIT}/login")
                page.get_by_label("Username").fill(username)
                page.get_by_label("Password").fill(password)
                page.get_by_role("button", name="Log in").click()

            if c == "shopping_admin":
                username = ACCOUNTS["shopping_admin"]["username"]
                password = ACCOUNTS["shopping_admin"]["password"]
                page.goto(f"{SHOPPING_ADMIN}")
                page.get_by_placeholder("user name").fill(username)
                page.get_by_placeholder("password").fill(password)
                page.get_by_role("button", name="Sign in").click()

            if c == "gitlab":
                username = ACCOUNTS["gitlab"]["username"]
                password = ACCOUNTS["gitlab"]["password"]
                page.goto(f"{GITLAB}/users/sign_in")
                page.screenshot(path="debug.png")
                page.get_by_test_id("username-field").click()
                page.get_by_test_id("username-field").fill(username)
                page.get_by_test_id("username-field").press("Tab")
                page.get_by_test_id("password-field").fill(password)
                page.get_by_test_id("sign-in-button").click()

            if c == "timeoff":
                username = ACCOUNTS["timeoff"]["username"]
                password = ACCOUNTS["timeoff"]["password"]
                page.goto(f"{TIMEOFF}/login/")
                page.wait_for_timeout(1000)
                page.fill('input[name="username"]', username)
                page.fill('input[name="password"]', password)
                page.click('button[type="submit"]:has-text("Login")')
                page.wait_for_timeout(2000)
                
            if c == "keystonejs":
                username = ACCOUNTS["keystonejs"]["username"]
                password = ACCOUNTS["keystonejs"]["password"]
                page.goto(f"{KEYSTONEJS}/keystone/signin")
                page.wait_for_timeout(1000)
                page.fill('input[name="email"]', username)
                page.fill('input[name="password"]', password)
                page.click('button[type="submit"]')
                page.wait_for_timeout(2000)

            if c == "nodebb":
                username = ACCOUNTS["nodebb"]["username"]
                password = ACCOUNTS["nodebb"]["password"]
                page.goto(f"{NODEBB}/login")
                page.wait_for_timeout(1000)
                page.fill('input[name="username"]', username)
                page.fill('input[name="password"]', password)
                page.click('button[type="submit"]')
                page.wait_for_timeout(2000)

            if c == "postmill":
                login_postmill(page)

            if c == "gadael":
                login_gadael(page)

            if c == "parabank":
                login_parabank(page)

            if c == "realworld":
                login_realworld(page)

            if c == "agilefant":
                login_agilefant(page)

            if c == "4gaboards":
                login_4gaboards(page)

            context.storage_state(path=f"{auth_folder}/{c}_state.json")
        finally:
            context_manager.__exit__(None, None, None)
    
    # Create combined cookie for multiple sites
    context_manager = sync_playwright()
    playwright = context_manager.__enter__()
    try:
        browser = playwright.chromium.launch(headless=HEADLESS)
        context = browser.new_context()
        page = context.new_page()

        if "shopping" in comb:
            username = ACCOUNTS["shopping"]["username"]
            password = ACCOUNTS["shopping"]["password"]
            page.goto(f"{SHOPPING}/customer/account/login/")
            page.get_by_label("Email", exact=True).fill(username)
            page.get_by_label("Password", exact=True).fill(password)
            page.get_by_role("button", name="Sign In").click()

        if "onestopshop" in comb:
            username = ACCOUNTS["onestopshop"]["username"]
            password = ACCOUNTS["onestopshop"]["password"]
            page.goto(f"{ONESTOPSHOP}/customer/account/login/")
            page.get_by_label("Email", exact=True).fill(username)
            page.get_by_label("Password", exact=True).fill(password)
            page.get_by_role("button", name="Sign In").click()

        if "reddit" in comb:
            username = ACCOUNTS["reddit"]["username"]
            password = ACCOUNTS["reddit"]["password"]
            page.goto(f"{REDDIT}/login")
            page.get_by_label("Username").fill(username)
            page.get_by_label("Password").fill(password)
            page.get_by_role("button", name="Log in").click()

        if "shopping_admin" in comb:
            username = ACCOUNTS["shopping_admin"]["username"]
            password = ACCOUNTS["shopping_admin"]["password"]
            page.goto(f"{SHOPPING_ADMIN}")
            page.get_by_placeholder("user name").fill(username)
            page.get_by_placeholder("password").fill(password)
            page.get_by_role("button", name="Sign in").click()

        if "gitlab" in comb:
            username = ACCOUNTS["gitlab"]["username"]
            password = ACCOUNTS["gitlab"]["password"]
            page.goto(f"{GITLAB}/users/sign_in")
            page.get_by_test_id("username-field").click()
            page.get_by_test_id("username-field").fill(username)
            page.get_by_test_id("username-field").press("Tab")
            page.get_by_test_id("password-field").fill(password)
            page.get_by_test_id("sign-in-button").click()

        if "timeoff" in comb:
            username = ACCOUNTS["timeoff"]["username"]
            password = ACCOUNTS["timeoff"]["password"]
            page.goto(f"{TIMEOFF}/login/")
            page.wait_for_timeout(1000)
            page.fill('input[name="username"]', username)
            page.fill('input[name="password"]', password)
            page.click('button[type="submit"]:has-text("Login")')
            page.wait_for_timeout(2000)
            
        if "keystonejs" in comb:
            username = ACCOUNTS["keystonejs"]["username"]
            password = ACCOUNTS["keystonejs"]["password"]
            page.goto(f"{KEYSTONEJS}/keystone/signin")
            page.wait_for_timeout(1000)
            page.fill('input[name="email"]', username)
            page.fill('input[name="password"]', password)
            page.click('button[type="submit"]')
            page.wait_for_timeout(2000)
            
        if "nodebb" in comb:
            username = ACCOUNTS["nodebb"]["username"]
            password = ACCOUNTS["nodebb"]["password"]
            page.goto(f"{NODEBB}/login")
            page.wait_for_timeout(1000)
            page.fill('input[name="username"]', username)
            page.fill('input[name="password"]', password)
            page.click('button[type="submit"]')
            page.wait_for_timeout(2000)

        if "postmill" in comb:
            login_postmill(page)

        if "gadael" in comb:
            login_gadael(page)

        if "parabank" in comb:
            login_parabank(page)

        if "realworld" in comb:
            login_realworld(page)

        if "agilefant" in comb:
            login_agilefant(page)

        if "4gaboards" in comb:
            login_4gaboards(page)

        context.storage_state(path=f"{auth_folder}/{'.'.join(comb)}_state.json")
    finally:
        context_manager.__exit__(None, None, None)


def get_site_comb_from_filepath(file_path: str) -> list[str]:
    comb = os.path.basename(file_path).rsplit("_", 1)[0].split(".")
    return comb


def main(auth_folder: str = "./.auth") -> None:
    pairs = list(combinations(SITES, 2))

    max_workers = 8
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for pair in pairs:
            # TODO[shuyanzh] auth don't work on these two sites
            if "reddit" in pair and (
                "shopping" in pair or "shopping_admin" in pair
            ):
                continue
            executor.submit(
                renew_comb, list(sorted(pair)), auth_folder=auth_folder
            )

        for site in SITES:
            executor.submit(renew_comb, [site], auth_folder=auth_folder)

    validation_jobs = []
    cookie_files = list(glob.glob(f"{auth_folder}/*.json"))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for c_file in cookie_files:
            comb = get_site_comb_from_filepath(c_file)
            for cur_site in comb:
                url = URLS[SITES.index(cur_site)]
                keyword = KEYWORDS[SITES.index(cur_site)]
                match = EXACT_MATCH[SITES.index(cur_site)]
                future = executor.submit(
                    is_expired, Path(c_file), url, keyword, match
                )
                validation_jobs.append((future, c_file, cur_site))

    for future, cookie_file, cur_site in validation_jobs:
        assert not future.result(), f"Cookie {cookie_file} expired for site '{cur_site}'."


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--site_list", nargs="+", default=["all"])
    parser.add_argument("--auth_folder", type=str, default="./.auth")
    args = parser.parse_args()
    if not args.site_list:
        main()
    else:
        if "all" in args.site_list:
            main(auth_folder=args.auth_folder)
        else:
            renew_comb(args.site_list, auth_folder=args.auth_folder)
