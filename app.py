import math
import os
from datetime import date
from pathlib import Path

import pandas as pd
from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import func

from models import db, User, CovidRecord


# -------------------- APP CONFIGURATION --------------------

app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ.get(
    "FLASK_SECRET_KEY",
    "dev-only-change-this-secret-key",
)

# Preserve the existing SQLite database.
Path(app.instance_path).mkdir(parents=True, exist_ok=True)
database_path = Path(app.instance_path) / "covid19.db"

app.config["SQLALCHEMY_DATABASE_URI"] = (
    "sqlite:///" + database_path.as_posix()
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)


# -------------------- CSV IMPORT --------------------

def import_csv():
    """Import the CSV only when the database has no COVID records."""

    if db.session.query(CovidRecord.id).first():
        app.logger.info("Existing COVID records found; skipping CSV import.")
        return

    csv_path = Path(app.root_path) / "data" / "covid19.csv"

    if not csv_path.exists():
        raise FileNotFoundError(f"Dataset not found: {csv_path}")

    df = pd.read_csv(csv_path)

    required = {"ZIP Code", "Week Start", "Cases - Weekly"}
    missing = required - set(df.columns)

    if missing:
        raise ValueError(
            f"Missing required CSV columns: {sorted(missing)}"
        )

    df["Week Start"] = pd.to_datetime(
        df["Week Start"], errors="coerce"
    )

    def number(row, column):
        value = row.get(column)

        if value is None or pd.isna(value):
            return None

        return float(value)

    batch = []

    try:
        for _, row in df.iterrows():
            if pd.isna(row["Week Start"]) or pd.isna(row["ZIP Code"]):
                continue

            batch.append(
                CovidRecord(
                    zip_code=str(row["ZIP Code"]).strip(),
                    week_start=row["Week Start"].date(),
                    cases_weekly=number(row, "Cases - Weekly"),
                    deaths_weekly=number(row, "Deaths - Weekly"),
                    tests_weekly=number(row, "Tests - Weekly"),
                    cases_cumulative=number(
                        row, "Cases - Cumulative"
                    ),
                )
            )

            if len(batch) >= 500:
                db.session.add_all(batch)
                db.session.commit()
                batch.clear()

        if batch:
            db.session.add_all(batch)
            db.session.commit()

    except Exception:
        db.session.rollback()
        raise


# -------------------- DATABASE INITIALIZATION --------------------

with app.app_context():
    db.create_all()
    import_csv()


# -------------------- HOME --------------------

@app.route("/")
def home():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))

    return redirect(url_for("signup"))


# -------------------- SIGNUP --------------------

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not username or not email or len(password) < 8:
            flash(
                "Enter all details. Password must be at least 8 characters.",
                "error",
            )
            return redirect(url_for("signup"))

        existing = User.query.filter(
            (User.username == username) | (User.email == email)
        ).first()

        if existing:
            flash("Username or email already exists.", "error")
            return redirect(url_for("signup"))

        user = User(username=username, email=email)
        user.set_password(password)

        db.session.add(user)
        db.session.commit()

        flash("Account created! Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("signup.html")


# -------------------- LOGIN --------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = User.query.filter_by(email=email).first()

        if user and user.check_password(password):
            session.clear()
            session["user_id"] = user.id
            session["username"] = user.username

            flash("Login successful!", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid email or password.", "error")

    return render_template("login.html")


# -------------------- DASHBOARD --------------------

@app.route("/dashboard")
def dashboard():
    if not session.get("user_id"):
        return redirect(url_for("login"))

    selected_zip = (
        request.args.get("zip_code", "all").strip() or "all"
    )
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()

    zip_rows = (
        db.session.query(CovidRecord.zip_code)
        .distinct()
        .order_by(CovidRecord.zip_code)
        .all()
    )

    zip_codes = [
        row[0] for row in zip_rows if row[0] is not None
    ]

    parsed_start = None
    parsed_end = None

    try:
        if start_date:
            parsed_start = date.fromisoformat(start_date)

        if end_date:
            parsed_end = date.fromisoformat(end_date)

        if (
            parsed_start
            and parsed_end
            and parsed_start > parsed_end
        ):
            flash(
                "Start date must be before or equal to end date.",
                "error",
            )
            start_date = ""
            end_date = ""
            parsed_start = None
            parsed_end = None

    except ValueError:
        flash("Please select valid dates.", "error")
        start_date = ""
        end_date = ""
        parsed_start = None
        parsed_end = None

    records_query = CovidRecord.query

    if selected_zip != "all":
        if selected_zip in zip_codes:
            records_query = records_query.filter(
                CovidRecord.zip_code == selected_zip
            )
        else:
            flash("The selected ZIP code was not found.", "error")
            selected_zip = "all"

    if parsed_start:
        records_query = records_query.filter(
            CovidRecord.week_start >= parsed_start
        )

    if parsed_end:
        records_query = records_query.filter(
            CovidRecord.week_start <= parsed_end
        )

    totals = records_query.with_entities(
        func.coalesce(func.sum(CovidRecord.cases_weekly), 0),
        func.coalesce(func.sum(CovidRecord.deaths_weekly), 0),
        func.coalesce(func.sum(CovidRecord.tests_weekly), 0),
    ).first()

    total_cases = round(totals[0] or 0)
    total_deaths = round(totals[1] or 0)
    total_tests = round(totals[2] or 0)

    weekly_rows = (
        records_query.with_entities(
            CovidRecord.week_start,
            func.sum(CovidRecord.cases_weekly).label(
                "weekly_cases"
            ),
        )
        .group_by(CovidRecord.week_start)
        .order_by(CovidRecord.week_start)
        .all()
    )

    chart_labels = [
        row.week_start.isoformat() for row in weekly_rows
    ]

    chart_cases = [
        round(row.weekly_cases or 0, 2)
        for row in weekly_rows
    ]

    prediction = session.pop("covid_prediction", None)
    prediction_last_week = session.pop(
        "prediction_last_week", None
    )

    return render_template(
        "dashboard.html",
        username=session.get("username"),
        total_cases=total_cases,
        total_deaths=total_deaths,
        total_tests=total_tests,
        chart_labels=chart_labels,
        chart_cases=chart_cases,
        zip_codes=zip_codes,
        selected_zip=selected_zip,
        start_date=start_date,
        end_date=end_date,
        prediction=prediction,
        prediction_last_week=prediction_last_week,
    )


# -------------------- COVID-19 BASELINE FORECAST --------------------

@app.route("/predict", methods=["POST"])
def predict():
    if not session.get("user_id"):
        return redirect(url_for("login"))

    selected_zip = (
        request.form.get("zip_code", "all").strip() or "all"
    )
    end_date_value = request.form.get("end_date", "").strip()
    start_date_value = request.form.get("start_date", "").strip()

    redirect_args = {
        "zip_code": selected_zip,
        "start_date": start_date_value,
        "end_date": end_date_value,
    }

    try:
        cutoff_date = (
            date.fromisoformat(end_date_value)
            if end_date_value
            else None
        )

        query = db.session.query(
            CovidRecord.week_start,
            func.sum(CovidRecord.cases_weekly).label("cases"),
        ).filter(
            CovidRecord.week_start.isnot(None),
            CovidRecord.cases_weekly.isnot(None),
        )

        if selected_zip.lower() != "all":
            query = query.filter(
                CovidRecord.zip_code == selected_zip
            )

        # Exclude observations after the selected end date.
        if cutoff_date:
            query = query.filter(
                CovidRecord.week_start <= cutoff_date
            )

        rows = (
            query.group_by(CovidRecord.week_start)
            .order_by(CovidRecord.week_start)
            .all()
        )

        if not rows:
            flash(
                "No historical case data found for this ZIP code and end date.",
                "error",
            )
            return redirect(
                url_for("dashboard", **redirect_args)
            )

        weekly = pd.Series(
            [float(row.cases or 0) for row in rows],
            index=pd.to_datetime(
                [row.week_start for row in rows]
            ),
            dtype=float,
        ).sort_index()

        weekly = weekly.resample("W-MON").sum().dropna()

        if weekly.empty:
            flash("No usable weekly case data was found.", "error")
            return redirect(
                url_for("dashboard", **redirect_args)
            )

        if len(weekly) < 2:
            flash(
                "At least two observed weeks are required for a baseline forecast.",
                "error",
            )
            return redirect(
                url_for("dashboard", **redirect_args)
            )

        latest_cases = float(weekly.iloc[-1])

        if not math.isfinite(latest_cases) or latest_cases < 0:
            raise ValueError("The latest weekly case count is invalid.")

        # Use the latest observed week's cases as a simple baseline
        # estimate for the following week.
        prediction = max(0, round(latest_cases))
        latest_week = weekly.index[-1].date().isoformat()

        session["covid_prediction"] = prediction
        session["prediction_last_week"] = latest_week

        app.logger.info(
            "Baseline forecast | scope=%s | cutoff=%s | "
            "latest_week=%s | latest_cases=%s | forecast=%s",
            (
                "all ZIP codes"
                if selected_zip.lower() == "all"
                else selected_zip
            ),
            cutoff_date or "latest available",
            latest_week,
            latest_cases,
            prediction,
        )

        flash(
            f"Baseline forecast: approximately {prediction:,} cases, "
            "based on the latest observed week.",
            "success",
        )

    except (ValueError, TypeError, OverflowError):
        app.logger.exception(
            "Invalid data while generating COVID-19 forecast."
        )
        session.pop("covid_prediction", None)
        session.pop("prediction_last_week", None)

        flash(
            "Could not generate the forecast. Check the selected date and data.",
            "error",
        )

    except Exception:
        app.logger.exception(
            "Unexpected error while generating COVID-19 forecast."
        )
        session.pop("covid_prediction", None)
        session.pop("prediction_last_week", None)

        flash(
            "Unable to generate the forecast. Check the VS Code terminal.",
            "error",
        )

    return redirect(url_for("dashboard", **redirect_args))


# -------------------- LOGOUT --------------------

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


# -------------------- RUN APPLICATION --------------------

if __name__ == "__main__":
    import webbrowser
    from threading import Timer

    Timer(
        1.5,
        lambda: webbrowser.open_new(
            "http://127.0.0.1:5000/signup"
        ),
    ).start()

    app.run(debug=True, use_reloader=False)