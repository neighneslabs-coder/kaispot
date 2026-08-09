"""Taking vouchers into stock, and putting sheets in front of agents.

Vouchers come in one way: the numbers on the printed A4 sheets are typed in
on the Add record page. The price is asked for each time, because a run
printed at 1,000 stays worth 1,000 after the package moves to 750.

A sheet uploaded for a named agent is registered and handed to her in the
same action - the sheet is her stock, so there is no second step to forget.
"""

from __future__ import annotations

from flask import (Blueprint, flash, redirect, render_template, request,
                   send_file, url_for)

from backend import auth
from backend.money import parse_date, parse_int, today
from backend.services import agents as agent_service
from backend.services import packages as package_service
from backend.services import sheets, stock as stock_service

bp = Blueprint("stock", __name__, url_prefix="/stock")


@bp.route("/")
@auth.staff_only
def index():
    return render_template("pages/stock.html",
                           overview=stock_service.overview(),
                           field=stock_service.in_the_field(),
                           totals=stock_service.totals(),
                           office=stock_service.office_stock(),
                           intakes=stock_service.intakes(30),
                           assignments=stock_service.assignments(limit=25),
                           packages=package_service.all_packages())


@bp.route("/upload")
@auth.staff_only
def upload():
    """The old PDF-intake address.

    Kept only so that any page still linking to it keeps working. Flask
    refuses to build a link to a route that no longer exists, and because the
    navigation sits in the shared layout, one stale link there takes down
    every page in the system. A redirect costs nothing and removes that whole
    class of breakage.
    """
    return redirect(url_for("stock.manual"))


@bp.route("/manual", methods=["GET", "POST"])
@auth.staff_only
def manual():
    """The Add record page: type the numbers off a printed sheet."""
    preview = None
    form = request.form if request.method == "POST" else {"received_on": today()}

    if request.method == "POST":
        package_id = parse_int(request.form.get("package_id"))
        package = package_service.get(package_id)
        # Blank price box means "whatever the package costs today".
        typed_price = request.form.get("price", "").strip()
        price = parse_int(typed_price) if typed_price else None
        agent_id = parse_int(request.form.get("agent_id")) or None

        try:
            codes = stock_service.split_codes(request.form.get("codes", ""))
            if not codes:
                raise ValueError("No voucher numbers were found in that box.")
            known = stock_service.known_codes(codes)
            fresh = [c for c in codes if c.lower() not in known]
            unit = price if price is not None else (
                package["price"] if package else 0)
            preview = {
                "codes": codes,
                "fresh": fresh,
                "duplicates": [c for c in codes if c.lower() in known],
                "package": package,
                "price": unit,
                "value": len(fresh) * unit,
                "agent": agent_service.get(agent_id) if agent_id else None,
            }

            if request.form.get("action") == "confirm":
                result = stock_service.take_in(
                    package_id=package_id, codes=codes, kind="manual",
                    received_on=parse_date(request.form.get("received_on")),
                    price=price,
                    reference=request.form.get("reference", ""),
                    note=request.form.get("note", ""),
                    agent_id=agent_id,
                    user_id=auth.current_user()["id"])
                auth.audit("stock-manual", f"{result['accepted']} codes")
                message = (f"{result['accepted']:,} vouchers registered at "
                           f"{result['price']:,} each, worth "
                           f"{result['value']:,}.")
                if result["agent"]:
                    message += f" Given straight to {result['agent']['name']}."
                if result["duplicates"]:
                    message += (f" {result['duplicates']:,} were already on "
                                f"record and were left alone.")
                flash(message, "ok")
                return redirect(url_for("stock.index"))
        except ValueError as exc:
            flash(str(exc), "error")

    return render_template("pages/stock_manual.html",
                           packages=package_service.all_packages(),
                           agents=agent_service.all_agents(),
                           preview=preview, form=form, today=today())


# --------------------------------------------------------------------------
# Sheets: a PDF handed to one named agent
# --------------------------------------------------------------------------

@bp.route("/sheets")
@auth.staff_only
def sheet_list():
    return render_template("pages/sheets.html", sheets=sheets.visible_to(None),
                           agents=agent_service.all_agents(),
                           packages=package_service.all_packages(),
                           today=today())


@bp.route("/sheets/upload", methods=["POST"])
@auth.staff_only
def sheet_upload():
    """Send a sheet to an agent and turn its cards into her stock.

    One action, because they are one act: handing over the paper *is* giving
    her the stock. Doing it in two steps meant a sheet could sit on her screen
    with nothing to sell against it, which is exactly what happened.
    """
    upload_file = request.files.get("sheet")
    agent_id = parse_int(request.form.get("agent_id")) or None
    package_id = parse_int(request.form.get("package_id")) or None
    count = parse_int(request.form.get("count"))
    pasted = request.form.get("pairs", "")
    prefix = request.form.get("prefix", "").strip()
    typed_price = request.form.get("price", "").strip()
    price = parse_int(typed_price) if typed_price else None
    day = parse_date(request.form.get("received_on"), today())

    agent = agent_service.get(agent_id) if agent_id else None
    package = package_service.get(package_id) if package_id else None

    if upload_file is None or not upload_file.filename:
        flash("Choose a PDF to upload.", "error")
        return redirect(url_for("stock.sheet_list"))
    if agent is None:
        # A sheet carries live vouchers. Putting it in front of every agent
        # would let any of them sell another's stock, so the owner is named.
        flash("Choose which agent this sheet belongs to.", "error")
        return redirect(url_for("stock.sheet_list"))
    if package is None:
        flash("Choose which package is printed on this sheet.", "error")
        return redirect(url_for("stock.sheet_list"))

    # Left blank, the sheet code is built from the agent and the date. Two
    # agents given sheets on the same day then never collide - a collision
    # made every card a duplicate and left the second agent with nothing.
    if not prefix:
        prefix = sheets.suggest_code(agent["code"], day)

    try:
        entries = sheets.parse_pairs(pasted)
        if entries:
            # Real usernames and passwords were pasted in, so use those. The
            # customer then gets the actual code, not a card number.
            cards = entries
        else:
            cards = sheets.card_codes(prefix, count)

        sheet_id = sheets.store(upload_file, request.form.get("title", ""),
                                agent_id, user_id=auth.current_user()["id"])
        result = stock_service.take_in(
            package_id=package_id, codes=cards, kind="pdf", received_on=day,
            price=price, reference=prefix.upper(),
            filename=upload_file.filename,
            pages=sheets.page_count(sheets.path_of(sheets.get(sheet_id))),
            agent_id=agent_id, user_id=auth.current_user()["id"])
        sheets.attach_intake(sheet_id, result["intake_id"])

        # Try to read the real usernames and passwords straight off the PDF.
        # The sheet teaches itself its own digits, so this works with no help
        # at all on a first upload, and after a naming session it works on
        # every card. Failure is not fatal - the agent can still sell by card
        # number while the office names the remaining shapes.
        stored_sheet = sheets.get(sheet_id)
        sheet_path = sheets.path_of(stored_sheet)
        try:
            pdf_result = sheets.fill_from_sheet(result["intake_id"],
                                                str(sheet_path))
        except Exception:
            pdf_result = None

    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("stock.sheet_list"))

    if not result["accepted"]:
        flash(f"None of those {result['duplicates']:,} cards were new - they "
              f"are already on record. If this is a fresh print run, give it a "
              f"different sheet code. {agent['name']} has not been given "
              f"anything.", "error")
        return redirect(url_for("stock.sheet_list"))

    auth.audit("sheet-issued", f"{result['accepted']} cards to agent {agent_id}")
    message = (f"{result['accepted']:,} cards at {result['price']:,} each "
               f"({result['value']:,}) given to {agent['name']}.")

    if pdf_result and pdf_result["filled"]:
        message += (f" {pdf_result['filled']:,} card(s) read directly from the "
                    f"PDF - the real username and password go straight to the "
                    f"customer.")
        if pdf_result["unreadable"]:
            message += (f" {pdf_result['unreadable']:,} still have letters the "
                        f"app has not seen before. Go to Codes to name them once "
                        f"and the rest will fill in.")
    else:
        message += (f" The PDF could not be read yet - go to Codes to name the "
                    f"letter shapes once, and from then on every sheet reads "
                    f"itself.")

    if result["duplicates"]:
        message += (f" {result['duplicates']:,} were already on record and "
                    f"were skipped.")
    flash(message, "ok")
    return redirect(url_for("stock.sheet_list"))
    if agent is None:
        # A sheet carries live vouchers. Putting it in front of every agent
        # would let any of them sell another's stock, so the owner is named.
        flash("Choose which agent this sheet belongs to.", "error")
        return redirect(url_for("stock.sheet_list"))
    if package is None:
        flash("Choose which package is printed on this sheet.", "error")
        return redirect(url_for("stock.sheet_list"))

    try:
        sheet_id = sheets.store(upload_file, request.form.get("title", ""),
                                agent_id, user_id=auth.current_user()["id"])
        codes = sheets.card_codes(prefix or request.form.get("title", ""), count)
        result = stock_service.take_in(
            package_id=package_id, codes=codes, kind="pdf", received_on=day,
            price=price, reference=prefix.strip().upper(),
            filename=upload_file.filename,
            pages=sheets.page_count(sheets.path_of(sheets.get(sheet_id))),
            agent_id=agent_id, user_id=auth.current_user()["id"])
        sheets.attach_intake(sheet_id, result["intake_id"])
        stored_sheet = sheets.get(sheet_id)
        sheet_path = sheets.path_of(stored_sheet)
        try:
            pdf_result = sheets.fill_from_sheet(result["intake_id"],
                                                str(sheet_path))
        except Exception:
            pdf_result = None
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("stock.sheet_list"))

    auth.audit("sheet-issued", f"{result['accepted']} cards to agent {agent_id}")
    message = (f"{result['accepted']:,} cards at {result['price']:,} each "
               f"({result['value']:,}) given to {agent['name']}.")
    if pdf_result and pdf_result["filled"]:
        message += (f" {pdf_result['filled']:,} card(s) read from the PDF - "
                    f"the real username and password go straight to the customer.")
        if pdf_result["unreadable"]:
            message += (f" {pdf_result['unreadable']:,} still need letters named "
                        f"- go to Codes.")
    else:
        message += " Go to Codes to name the letter shapes once."
    if result["duplicates"]:
        message += (f" {result['duplicates']:,} were already on record and skipped.")
    flash(message, "ok")
    return redirect(url_for("stock.sheet_list"))


@bp.route("/sheets/<int:sheet_id>")
@auth.login_required
def sheet_open(sheet_id: int):
    sheet = sheets.get(sheet_id)
    if sheet is None:
        flash("That sheet is not there.", "error")
        return redirect(auth.home_for(auth.current_user()))

    user = auth.current_user()
    if not sheets.may_open(sheet, user):
        flash("That sheet is not yours.", "error")
        return redirect(auth.home_for(user))

    path = sheets.path_of(sheet)
    if not path.exists():
        flash("That file is missing from the uploads folder.", "error")
        return redirect(auth.home_for(user))
    return send_file(path, mimetype="application/pdf",
                     download_name=sheet["filename"])


@bp.route("/sheets/<int:sheet_id>/codes", methods=["GET", "POST"])
@auth.staff_only
def sheet_codes(sheet_id: int):
    """Fill the real usernames and passwords onto a sheet's cards.

    A sheet printed to PDF as a picture has no readable text, so the
    credentials cannot be lifted out of the file. But Mikhmon shows the same
    list on a web page: selecting it, copying, and pasting it here matches
    the pairs onto cards 1, 2, 3 in printed order.
    """
    sheet = sheets.get(sheet_id)
    if sheet is None:
        flash("That sheet is not there.", "error")
        return redirect(url_for("stock.sheet_list"))
    if not sheet["intake_id"]:
        flash("That sheet has no cards registered against it, so there is "
              "nothing to fill in.", "error")
        return redirect(url_for("stock.sheet_list"))

    result = None
    reading = None
    pasted = ""
    path = sheets.path_of(sheet)

    if request.method == "POST" and request.form.get("action") in (
            "read", "learn", "apply"):
        action = request.form.get("action")
        try:
            if action == "learn":
                names = {}
                for key, value in request.form.items():
                    if key.startswith("name_") and value.strip():
                        names[parse_int(key[len("name_"):])] = value.strip()
                added = sheets.learn(str(path), names)
                flash(f"{added} shape(s) named. They are remembered for every "
                      f"sheet from now on.", "ok")
            elif action == "apply":
                done = sheets.fill_from_sheet(sheet["intake_id"], str(path))
                message = (f"{done['filled']:,} card(s) filled in straight from "
                           f"the sheet.")
                if done["unreadable"]:
                    message += (f" {done['unreadable']:,} still have letters "
                                f"nobody has named yet.")
                flash(message, "ok" if done["filled"] else "warn")
                return redirect(url_for("stock.sheet_codes", sheet_id=sheet_id))
            reading = sheets.read_sheet(str(path))
        except Exception as exc:
            flash(f"That sheet could not be read: {exc}", "error")
        return render_template("pages/sheet_codes.html", sheet=sheet,
                               cards=sheets.cards_of(sheet["intake_id"]),
                               result=None, pasted="", reading=reading,
                               svg=sheets.outline_svg)

    if request.method == "POST":
        pasted = request.form.get("pairs", "")
        try:
            pairs = sheets.read_pairs(pasted)
            if request.form.get("action") == "confirm":
                result = sheets.fill_credentials(sheet["intake_id"], pairs)
                auth.audit("sheet-codes-filled",
                           f"sheet {sheet_id}: {result['filled']}")
                message = (f"{result['filled']:,} card(s) now carry their real "
                           f"username and password.")
                if result["short"]:
                    message += (f" {result['short']:,} card(s) at the end were "
                                f"left as they were - paste the rest to finish "
                                f"them.")
                if result["spare"]:
                    message += (f" {result['spare']:,} extra pair(s) were "
                                f"ignored: the sheet only has "
                                f"{result['cards']:,} cards.")
                flash(message, "ok")
                return redirect(url_for("stock.sheet_list"))
            cards = sheets.cards_of(sheet["intake_id"])
            result = {
                "cards": len(cards), "pasted": len(pairs),
                "short": max(0, len(cards) - len(pairs)),
                "spare": max(0, len(pairs) - len(cards)),
                "preview": [{"code": c["code"], **p}
                            for c, p in zip(cards[:8], pairs[:8])],
                "checked": True,
            }
        except ValueError as exc:
            flash(str(exc), "error")

    return render_template("pages/sheet_codes.html", sheet=sheet,
                           cards=sheets.cards_of(sheet["intake_id"]),
                           result=result, pasted=pasted, reading=reading,
                           svg=sheets.outline_svg)


@bp.route("/sheets/<int:sheet_id>/assign", methods=["POST"])
@auth.staff_only
def sheet_assign(sheet_id: int):
    """Give a sheet an owner. Fixes anything uploaded before an owner was
    required, without having to upload the file again."""
    agent_id = parse_int(request.form.get("agent_id")) or None
    try:
        if agent_id is None or agent_service.get(agent_id) is None:
            raise ValueError("Choose which agent this sheet belongs to.")
        sheets.assign(sheet_id, agent_id)
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        agent = agent_service.get(agent_id)
        auth.audit("sheet-assigned", f"sheet {sheet_id} -> agent {agent_id}")
        flash(f"That sheet now belongs to {agent['name']}. It will show on her "
              f"selling screen.", "ok")
    return redirect(url_for("stock.sheet_list"))


@bp.route("/sheets/<int:sheet_id>/delete", methods=["POST"])
@auth.staff_only
def sheet_delete(sheet_id: int):
    sheets.delete(sheet_id)
    auth.audit("sheet-deleted", str(sheet_id))
    flash("Sheet removed.", "ok")
    return redirect(url_for("stock.sheet_list"))