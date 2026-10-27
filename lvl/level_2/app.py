import os

from flask import Flask, abort, render_template, session

from common.solve_flask import register_solve

app = Flask(__name__)
register_solve(app)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or os.urandom(32)

LOG_DIR = os.environ.get("LOG_DIR", "/app/logs")
FLAG_LOG = os.path.join(LOG_DIR, "flags.log")

USERS = {
    1001: {"name": "Алиса", "initials": "А"},
    1002: {"name": "Борис", "initials": "Б"},
}

INVOICES = {
    7001: {
        "owner_id": 1001,
        "customer": "Алиса",
        "item": "Механическая клавиатура",
        "amount": "4 900 ₽",
        "status": "Оплачен",
        "date": "14 октября 2026",
        "description": "Периферия и аксессуары",
    },
    7002: {
        "owner_id": 1002,
        "customer": "Борис",
        "item": "Рабочая станция",
        "amount": "184 900 ₽",
        "status": "Ожидает оплаты",
        "date": "16 октября 2026",
        "description": "Компьютерное оборудование",
    },
}


def report_success(note):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)

        with open(FLAG_LOG, "a", encoding="utf-8") as log:
            log.write(f"[FLAG_SUCCESS] {note}\n")

    except OSError:
        app.logger.exception("Failed to write success event")


@app.before_request
def create_demo_session():
    session.setdefault("user_id", 1001)


@app.get("/")
def index():
    user_id = session["user_id"]
    user = USERS[user_id]

    own_invoices = [
        (invoice_id, invoice)
        for invoice_id, invoice in INVOICES.items()
        if invoice["owner_id"] == user_id
    ]

    return render_template(
        "index.html",
        user=user,
        invoices=own_invoices,
    )


@app.get("/invoice/<int:invoice_id>")
def invoice_detail(invoice_id):
    invoice = INVOICES.get(invoice_id)

    if invoice is None:
        abort(404)

    current_user_id = session["user_id"]


    if invoice["owner_id"] != current_user_id:
        report_success("level-2-idor")

    return render_template(
        "invoice.html",
        invoice_id=invoice_id,
        invoice=invoice,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)