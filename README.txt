KAISPOT - Voucher Management System        v3.0
===============================================

Runs on one office PC. Staff use it from that PC; sales agents reach it from
their phones over the company WiFi.


STARTING IT
-----------
Windows   double-click start.bat
Linux/Mac ./start.sh

The first run installs three libraries and takes a minute. After that it
starts in a couple of seconds and opens your browser by itself.

First sign-in is  admin / admin  and it will make you set a real password
straight away. Leave the black window open while people are using it.


LETTING AGENTS REACH IT
-----------------------
The start window prints the address to give them, for example
http://192.168.88.10:8080

Two things on your side:

1. Give this PC a fixed address on the router, so the link does not change.

2. Add it to the MikroTik walled garden, so agents can open it before they
   have logged in to the hotspot. The exact command is printed for you when
   the server starts:

       /ip hotspot walled-garden ip
       add action=accept dst-address=192.168.88.10 comment="KAISPOT"

3. On Windows, allow Python through the firewall on private networks the
   first time it asks. If nobody can connect, that prompt was probably
   dismissed - check Windows Defender Firewall.


HOW A DAY WORKS
---------------
Daily operations is the screen the office lives on. One row per agent:

    opening      what she was holding when the day started
    top-up       value of vouchers handed to her during the day
    collection   cash she handed in
    closing      opening + top-up - collection
    shortage     closing minus the stock actually still in her hands

Only the collection is typed. Opening comes from the voucher records, and
top-up is whatever was given out that day.

The shortage falls out of describing the same stock two ways. If she sold
60,000 worth and handed in 40,000, the closing figure says she should still
hold 60,000 of vouchers while the voucher records say she holds 40,000. That
20,000 gap is cash that has not arrived.

Closing a day puts any shortage on her account as a debt, credits her
commission on the cash that did arrive, and carries her real stock into
tomorrow. It is final; an administrator can reopen a day, which reverses
every entry it made rather than deleting them.


TWO WAYS TO TAKE VOUCHERS IN
----------------------------
Both can be used at once, on the same packages, on the same day.

  Upload a PDF sheet   The sheet from Mikhmon is read and its codes counted
                       automatically. You see what was found and approve it
                       before anything is taken in.

  Add record by hand   For printed paper sheets. Type one number per line, or
                       a range like KS0001-KS0100.

Stock cannot be doubled. Every voucher code is stored once and the code
column is unique across the whole system, so a number that arrives twice -
by whichever route - is reported as a duplicate and left alone.

If a PDF gives roughly double the count you expect, it is printing a password
beside every code and both are being picked up. Set a code prefix on the
package (WK, DAY and so on) and upload again: the pattern is then built from
that prefix and counts only the codes.


PACKAGES
--------
Add, rename, reprice and retire packages whenever you like. Price is copied
onto each voucher when it is taken in, so changing a package price tomorrow
never revalues stock already in the field or alters what an agent owes on it.
A package with vouchers on record cannot be deleted, only switched off.


THE AGENT'S SCREEN
------------------
Give an agent a sign-in from her profile. She signs in on the office WiFi and
lands on her point of sale:

  - her stock, one card per package, with a Sell one button
  - selling takes the next voucher, marks it sold and shows the code
  - WhatsApp, Telegram and SMS buttons open the app on her phone with the
    message already written; nothing is sent by the server, so no gateway,
    no API keys and no internet are needed
  - saved customers, so a regular number is not retyped
  - PDF sheets the office has shared with her
  - her progress against today's and this month's targets

She can only ever see her own figures.


WHEN AN AGENT PAYS A DEBT
-------------------------
Record it on her profile. Debt, commission, her page, the daily table, the
dashboard and the company totals all move at once, because every one of them
is a sum over the same ledger table. There is no second place to update and
so no way for them to disagree.

Commission is earned on cash actually received, including late payments. An
agent who hands in short earns proportionally less, and clearing the debt
later earns the rest - so there is always a reason to bring the money.


BACKUPS
-------
Settings -> Back up now writes a dated copy into the backups folder. Do it
weekly and copy the files onto a flash disk. A backup on the same PC does not
survive that PC being stolen.


THE FOLDERS
-----------
backend/     the application: Python only
frontend/    templates and stylesheet: no Python
data/        the database and uploaded PDFs   <- this is your business
backups/     dated copies

Re-running install.py refreshes the code and never touches data/.


IF SOMETHING GOES WRONG
-----------------------
Nobody can connect      Check the Windows firewall prompt was allowed, and
                        that the walled-garden rule points at this PC's
                        current address.
Forgotten password      Another administrator can reset it in Settings.
A day was closed wrongly An administrator can reopen it.
PDF reads nothing       It is a scan, not a generated sheet. Type the numbers
                        in on Add record by hand.
