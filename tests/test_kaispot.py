"""End-to-end tests for KAISPOT v3.

Runs a whole working day through the real web app: take vouchers in by both
routes, give them to an agent, sell some electronically, record the cash,
close the day, then clear the debt and check every figure moved together.

Run with:  python -m tests.test_kaispot
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

FAILURES: list[str] = []
SHEET = "/tmp/mikhmon_sheet.pdf"


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  ok    {label}")
    else:
        FAILURES.append(label)
        print(f"  FAIL  {label}: got {got!r}, wanted {want!r}")


# --------------------------------------------------------------------------

def test_arithmetic() -> None:
    from backend.services.daily import figures
    from backend.services.ledger import commission_on
    from backend.money import clean_phone

    print("\nThe daily line")
    d = figures(opening=100_000, topup=200_000, collection=250_000,
                on_hand=50_000, rate_bp=1000)
    check("closing = opening + top-up - collection", d["closing"], 50_000)
    check("sold = opening + top-up - stock still held", d["sold"], 250_000)
    check("a balanced day has no shortage", d["shortage"], 0)
    check("commission is 10% of the cash", d["commission"], 25_000)

    print("\nA day where cash goes missing")
    d = figures(opening=100_000, topup=0, collection=40_000, on_hand=40_000,
                rate_bp=1000)
    check("she sold 60,000 worth", d["sold"], 60_000)
    check("but handed in only 40,000, so 20,000 is short", d["shortage"], 20_000)
    check("and earns commission only on what arrived", d["commission"], 4_000)

    print("\nHanding in short must never pay better")
    full = figures(100_000, 0, 60_000, 40_000, 1000)
    short = figures(100_000, 0, 40_000, 40_000, 1000)
    check("6,000 beats 4,000", full["commission"] > short["commission"], True)

    print("\nPhone numbers for the share links")
    check("a local number becomes international",
          clean_phone("0771 234 567"), "256771234567")
    check("an international one is left alone",
          clean_phone("+256771234567"), "256771234567")

    print("\nCommission")
    check("10% of 250,000", commission_on(250_000, 1000), 25_000)
    check("nothing collected, nothing earned", commission_on(0, 1000), 0)


def test_codes_and_pdf() -> None:
    from backend.services.stock import split_codes
    from backend.services import pdfread

    print("\nTyping voucher numbers in by hand")
    check("a range expands with its numbering",
          split_codes("KS0008-KS0010"), ["KS0008", "KS0009", "KS0010"])
    check("several to a line", split_codes("AB12 CD34, EF56"),
          ["AB12", "CD34", "EF56"])
    check("repeats are dropped", split_codes("AA1\nAA1\nBB2"), ["AA1", "BB2"])
    try:
        split_codes("A1-A99999")
        check("a silly range is refused", "allowed", "refused")
    except ValueError:
        check("a silly range is refused", "refused", "refused")

    if Path(SHEET).exists():
        print("\nReading a generated PDF sheet")
        loose = pdfread.inspect(SHEET)
        check("the generic pattern reads the sheet", loose["text_found"], True)
        precise = pdfread.inspect(SHEET, pdfread.suggest_pattern("WK"))
        check("a package prefix finds exactly the 96 vouchers",
              precise["count"], 96)
        check("and they are in printed order",
              (precise["codes"][0], precise["codes"][-1]),
              ("WK00001", "WK00096"))
        check("the prefix pattern beats the generic one here",
              precise["count"] < loose["count"], True)


# --------------------------------------------------------------------------

def run_app_tests() -> None:
    sandbox = Path(tempfile.mkdtemp(prefix="kaispot-test-"))
    from backend import config
    config.DATA_DIR = sandbox
    config.UPLOAD_DIR = sandbox / "uploads"
    config.DB_PATH = sandbox / "t.db"
    config.SECRET_PATH = sandbox / "k"
    config.BACKUP_DIR = sandbox / "b"
    import backend.db as db
    db.DB_PATH, db.DATA_DIR = config.DB_PATH, config.DATA_DIR
    db.BACKUP_DIR, db.UPLOAD_DIR = config.BACKUP_DIR, config.UPLOAD_DIR
    import backend.services.sheets as sheet_service
    sheet_service.UPLOAD_DIR = config.UPLOAD_DIR
    import backend.routes.stock as stock_routes
    stock_routes.UPLOAD_DIR = config.UPLOAD_DIR

    from backend import seed
    from backend.app import create_app
    seed.bootstrap()
    app = create_app()
    app.config["TESTING"] = True
    office = app.test_client()

    print("\nSigning in")
    check("a wrong password is refused",
          office.post("/sign-in", data={"username": "admin",
                                        "password": "no"}).status_code, 401)
    landing = office.post("/sign-in", data={"username": "admin",
                                            "password": "admin"},
                          follow_redirects=True)
    check("admin/admin gets in", landing.status_code, 200)
    check("and must set a password", b"Set your own password" in landing.data, True)
    office.post("/password", data={"new": "kaispot123", "confirm": "kaispot123"},
                follow_redirects=True)
    print("  ok    password changed")

    print("\nPackages can be added and edited at will")
    office.post("/packages/", data={"name": "Weekly Plus", "price": "12000",
                                    "validity": "8 days", "prefix": "WKP"})
    with app.app_context():
        from backend.services import packages
        made = packages.by_name("Weekly Plus")
        check("a new package appears", made["price"], 12_000)
        office.post(f"/packages/{made['id']}",
                    data={"name": "Weekly Plus", "price": "13000",
                          "validity": "8 days", "prefix": "WKP",
                          "colour": "#0b6e4f", "sort_order": "50", "active": "1"})
        check("and can be repriced", packages.by_name("Weekly Plus")["price"],
              13_000)
        weekly = packages.by_name("Weekly")
        weekly_id = weekly["id"]

    print("\nTaking stock in by hand, off a paper sheet")
    office.post("/stock/manual", data={"package_id": weekly_id,
                                       "codes": "PAP001-PAP050",
                                       "received_on": "2026-08-03",
                                       "action": "confirm"})
    with app.app_context():
        from backend.services import stock
        check("50 paper vouchers are in the office",
              stock.office_count(weekly_id), 50)

    print("\nTaking stock in from a PDF, counted automatically")
    if Path(SHEET).exists():
        from backend.services import pdfread
        with open(SHEET, "rb") as handle:
            office.post("/stock/upload", data={
                "package_id": weekly_id, "received_on": "2026-08-03",
                "pattern": pdfread.suggest_pattern("WK"), "action": "confirm",
                "sheet": (handle, "week.pdf")},
                content_type="multipart/form-data")
        with app.app_context():
            from backend.services import stock
            check("the sheet's 96 vouchers joined the 50 typed in",
                  stock.office_count(weekly_id), 146)

        print("\n  The two routes must not double the stock")
        with open(SHEET, "rb") as handle:
            office.post("/stock/upload", data={
                "package_id": weekly_id, "received_on": "2026-08-03",
                "pattern": pdfread.suggest_pattern("WK"), "action": "confirm",
                "sheet": (handle, "week-again.pdf")},
                content_type="multipart/form-data")
        with app.app_context():
            from backend.services import stock
            check("uploading the same sheet again adds nothing",
                  stock.office_count(weekly_id), 146)
        overlap = office.post("/stock/manual",
                              data={"package_id": weekly_id,
                                    "codes": "WK00001\nWK00002\nPAP999",
                                    "received_on": "2026-08-03",
                                    "action": "confirm"},
                              follow_redirects=True)
        check("typing in numbers already read off the PDF adds only the new one",
              b"were already on record" in overlap.data, True)
        with app.app_context():
            from backend.services import stock
            check("so the count rose by exactly one",
                  stock.office_count(weekly_id), 147)

    print("\nAn agent, and stock in her hands")
    office.post("/agents/new", data={"code": "A-01", "name": "Namono Grace",
                                     "phone": "0771000001", "station": "Kireka",
                                     "commission_rate": "1000",
                                     "monthly_target": "3000000",
                                     "daily_target": "100000",
                                     "credit_limit": "50000"})
    with app.app_context():
        from backend.services import agents
        agent = agents.all_agents()[0]
        agent_id = agent["id"]
        check("the agent was created", agent["name"], "Namono Grace")

    office.post("/daily/assign", data={"agent_id": agent_id,
                                       "package_id": weekly_id, "qty": "20",
                                       "day": "2026-08-05"})
    with app.app_context():
        from backend.services import agents
        check("she holds 20 vouchers worth 200,000",
              agents.stock_value(agent_id), 200_000)

    too_many = office.post("/daily/assign",
                           data={"agent_id": agent_id, "package_id": weekly_id,
                                 "qty": "99999", "day": "2026-08-05"},
                           follow_redirects=True)
    check("giving out more than the office holds is blocked",
          b"are in the office" in too_many.data, True)

    print("\nHer point of sale")
    office.post(f"/agents/{agent_id}/login")
    with app.app_context():
        from backend.db import one
        login = one("SELECT * FROM users WHERE agent_id = ?", (agent_id,))
        check("a sign-in was made for her", login["username"], "a-01")

    till = app.test_client()
    till.post("/sign-in", data={"username": "a-01", "password": "changeme"})
    till.post("/password", data={"new": "grace123", "confirm": "grace123"},
              follow_redirects=True)
    home = till.get("/sell/")
    check("she lands on her till", home.status_code, 200)
    check("and sees her stock", b"Weekly" in home.data, True)

    till.post("/sell/clients", data={"name": "Mama Shop", "phone": "0700111222"})
    with app.app_context():
        from backend.services import clients
        saved = clients.for_agent(agent_id)
        check("she can save a customer", len(saved), 1)
        check("with the number cleaned up for WhatsApp",
              saved[0]["phone"], "256700111222")
        client_id = saved[0]["id"]

    sold = till.post("/sell/sell", data={"package_id": weekly_id,
                                         "client_id": client_id,
                                         "channel": "whatsapp"})
    check("selling one works", sold.status_code, 200)
    check("the code is shown", b"voucher-code" in sold.data, True)
    check("with a WhatsApp link to that customer",
          b"wa.me/256700111222" in sold.data, True)
    check("and a Telegram link", b"t.me/share" in sold.data, True)

    for _ in range(5):
        till.post("/sell/sell", data={"package_id": weekly_id,
                                      "channel": "counter"})
    with app.app_context():
        from backend.services import agents, sales
        check("six vouchers are gone from her stock",
              agents.stock_value(agent_id), 140_000)
        check("and are recorded as sold today",
              sales.sales_on(agent_id, __import__("backend.money",
                                                  fromlist=["x"]).today())["qty"], 6)

    print("\nAgents may not wander outside their own screen")
    check("she cannot open the dashboard",
          till.get("/dashboard").status_code, 302)
    check("nor settings", till.get("/settings/").status_code, 302)
    check("nor the daily table", till.get("/daily/").status_code, 302)
    stranger = app.test_client()
    check("a signed-out visitor gets nothing",
          stranger.get("/daily/").status_code, 302)

    print("\nThe office records the cash")
    from backend.money import today as today_fn
    day = today_fn()
    office.post("/daily/", data={"day": day, f"cash_{agent_id}": "40,000"})
    with app.app_context():
        from backend.services import daily
        row = daily.get_row(agent_id, day)
        check("the typed '40,000' was understood", row["collection"], 40_000)
        check("she sold 60,000 worth", row["sold"], 60_000)
        check("closing = opening + top-up - collection", row["closing"], 160_000)
        check("she is holding 140,000", row["on_hand"], 140_000)
        check("so 20,000 is short", row["shortage"], 20_000)

    print("\nClosing the day")
    office.post(f"/daily/{agent_id}", data={"day": day, "collection": "40000",
                                            "action": "close"},
                follow_redirects=True)
    with app.app_context():
        from backend.services import ledger
        position = ledger.position(agent_id)
        check("the shortage became a debt", position["debt"], 20_000)
        check("commission was earned on the cash that arrived",
              position["commission_due"], 4_000)

    print("\nA payment must move every figure at once")
    before = office.get("/dashboard").data
    check("the dashboard shows the debt before", b"20,000" in before, True)
    office.post(f"/agents/{agent_id}/pay",
                data={"amount": "20000", "entry_date": day,
                      "note": "Brought the balance"}, follow_redirects=True)
    with app.app_context():
        from backend.services import ledger
        position = ledger.position(agent_id)
        check("her debt is cleared", position["debt"], 0)
        check("commission rose to the full 6,000 on 60,000 sold",
              position["commission_due"], 6_000)
        check("the company total moved with her",
              ledger.company_totals()["debt"], 0)
        check("and nobody is left in debt",
              ledger.company_totals()["agents_in_debt"], 0)

    print("\nReopening a closed day reverses exactly what it posted")
    office.post(f"/daily/{agent_id}", data={"day": day, "action": "reopen"},
                follow_redirects=True)
    with app.app_context():
        from backend.services import ledger
        position = ledger.position(agent_id)
        check("the shortage entry was reversed", position["debt"], -20_000)
        check("and so was the day's commission",
              position["commission_due"], 2_000)

    print("\nDeleting agents")
    office.post("/agents/new", data={"code": "A-99", "name": "Never Traded",
                                     "commission_rate": "1000"})
    with app.app_context():
        from backend.services import agents
        spare = [a for a in agents.all_agents() if a["code"] == "A-99"][0]
    office.post(f"/agents/{spare['id']}/delete", follow_redirects=True)
    with app.app_context():
        from backend.services import agents
        check("an agent who never traded is removed outright",
              agents.get(spare["id"]), None)
    kept = office.post(f"/agents/{agent_id}/delete", follow_redirects=True)
    check("one with history is switched off instead, not deleted",
          b"switched off rather than deleted" in kept.data, True)
    with app.app_context():
        from backend.services import agents
        check("and her record survives", agents.get(agent_id)["active"], 0)
        office.post(f"/agents/{agent_id}/edit",
                    data={"name": "Namono Grace", "commission_rate": "1000",
                          "active": "1"})
        check("she can be switched back on", agents.get(agent_id)["active"], 1)

    print("\nEvery page renders")
    for url in ["/dashboard", "/daily/", f"/daily/{agent_id}", "/agents/",
                "/agents/new", f"/agents/{agent_id}", f"/agents/{agent_id}/edit",
                "/packages/", "/stock/", "/stock/manual", "/stock/upload",
                "/stock/sheets", "/reports/", "/settings/", "/password",
                f"/sell/?agent={agent_id}",
                "/reports/export/agents.csv", "/reports/export/packages.csv",
                "/reports/export/debts.csv"]:
        check(f"GET {url}", office.get(url).status_code, 200)
    for period in ("day", "week", "month", "year"):
        check(f"dashboard {period}",
              office.get(f"/dashboard?period={period}").status_code, 200)
    check("an unknown address gives a clean 404",
          office.get("/nope").status_code, 404)

    print("\nUploading a sheet for an agent to read")
    if Path(SHEET).exists():
        with open(SHEET, "rb") as handle:
            office.post(f"/agents/{agent_id}/sheet",
                        data={"title": "This week", "sheet": (handle, "w.pdf")},
                        content_type="multipart/form-data")
        with app.app_context():
            from backend.services import sheets
            visible = sheets.visible_to(agent_id)
            check("she can see it", len(visible) >= 1, True)
            sheet_id = visible[0]["id"]
        # Deactivating her earlier also switched her sign-in off, which is
        # deliberate; an administrator turns it back on separately.
        with app.app_context():
            from backend.db import execute
            execute("UPDATE users SET active = 1 WHERE agent_id = ?", (agent_id,))
        till.post("/sign-in", data={"username": "a-01", "password": "grace123"})
        opened = till.get(f"/stock/sheets/{sheet_id}")
        check("and open the PDF itself", opened.status_code, 200)
        check("which really is a PDF", opened.data[:4], b"%PDF")

    shutil.rmtree(sandbox, ignore_errors=True)


if __name__ == "__main__":
    test_arithmetic()
    test_codes_and_pdf()
    run_app_tests()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for name in FAILURES:
            print(f"  - {name}")
        sys.exit(1)
    print("Everything behaves.")
