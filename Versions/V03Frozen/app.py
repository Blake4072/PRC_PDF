
from flask import Flask, render_template, request, redirect, url_for, session, flash
from datetime import timedelta, date
import uuid
import re
import processScreen1DatEntry as processor

app = Flask(__name__)
app.secret_key = "change-this-secret-key"
app.permanent_session_lifetime = timedelta(hours=8)

def ensure_user_session():
    if "user_id" not in session:
        session["user_id"] = str(uuid.uuid4())
    if "form_data" not in session:
        session["form_data"] = {}

@app.route("/", methods=["GET"])
def index():
    ensure_user_session()
    data = session.get("form_data", {})
    # Default requested_date to today's date if missing/blank
    if not data.get("requested_date"):
        data["requested_date"] = date.today().isoformat()
        session["form_data"] = data
    return render_template("form.html", data=data)


@app.post("/submit")
def submit():
    ensure_user_session()
    form_fields = request.form.to_dict(flat=True)

    errors = []
    cost_center = form_fields.get("cost_center", "").strip()
    facility = form_fields.get("facility", "").strip()
    email = form_fields.get("emails", "").strip()

    if not cost_center or not facility:
        errors.append("Cost Center # and Facility should be entered")

    # Email is required and must be blessinghealth.org
    if not email:
        errors.append("Email address has to be entered (xxx@blessinghealth.org)")
    else:
        import re
        if not re.match(r"^[^@\s]+@blessinghealth\.org$", email, flags=re.IGNORECASE):
            errors.append("Email address has to be of format xx@blessinghealth.org")

    # Persist entries so user doesn't lose work
    session["form_data"] = form_fields

    if errors:
        for e in errors:
            flash(e, "error")
        return redirect(url_for("index"))

    result = processor.process(form_fields)
    flash("Submitted successfully (demo). Processor returned: " + result[:200], "success")
    return redirect(url_for("index"))
@app.post("/clear")
def clear():
    ensure_user_session()
    # Reset all fields
    session["form_data"] = {}
    flash("Form cleared.", "success")
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
