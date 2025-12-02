from flask import Flask, request, redirect, url_for, render_template, session, jsonify, flash
import sqlite3
from datetime import datetime
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app = Flask(__name__)
app.secret_key = "cambia-esta-clave-por-una-mas-segura"  # Cámbiala en producción

# ======= CONFIG ADMIN (mejor usar variables de entorno)
# Por defecto dejo tu correo como admin; cambia la contraseña o usa variables de entorno.
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "nadiaibarracitas@gmail.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "TuAdmin123!")  # cámbiala pronto

# --- Email config (usa variables de entorno si prefieres)
EMAIL_USER = os.environ.get("EMAIL_USER", "nadiaibarracitas@gmail.com")
EMAIL_PASS = os.environ.get("EMAIL_PASS", "arsz ilhr bbao yynl")  # tu clave de aplicación

DB_PATH = "appointments.db"

# ---------------------------
# FUNCION PARA ENVIAR EMAIL
# ---------------------------
def send_email(to, subject, html_content):
    try:
        msg = MIMEMultipart()
        msg["From"] = EMAIL_USER
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(html_content, "html"))

        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASS)
        server.sendmail(EMAIL_USER, to, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print("Error enviando correo:", e)
        return False

# ---------- DB helpers ----------
def connect_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = connect_db()
    c = conn.cursor()
    # appointments table
    c.execute("""
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT,
            scheduled_at TEXT NOT NULL,
            notes TEXT,
            status TEXT DEFAULT 'scheduled',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    # slots table: fecha (YYYY-MM-DD), hora (HH:MM), available (0/1)
    c.execute("""
        CREATE TABLE IF NOT EXISTS slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            available INTEGER DEFAULT 1,
            note TEXT
        );
    """)
    conn.commit()
    conn.close()

init_db()

# ---------- auth helpers ----------
def is_logged_in():
    return session.get("admin_logged") is True

def login_required(fn):
    from functools import wraps
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not is_logged_in():
            flash("Debes iniciar sesión para ver esa página.")
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)
    return wrapper

# ---------- ROUTES: Public (cliente) ----------
@app.route("/")
def index():
    # Página con calendario; los datos se piden por AJAX a /api/slots
    return render_template("index.html")

# API: devolver slots como JSON para FullCalendar o para la UI
@app.route("/api/slots")
def api_slots():
    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, date, time, available, note FROM slots ORDER BY date, time")
    rows = cursor.fetchall()
    conn.close()
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "date": r["date"],
            "time": r["time"],
            "available": bool(r["available"]),
            "note": r["note"]
        })
    return jsonify(out)

# Reservar un slot por id (POST desde cliente)
@app.route("/book/<int:slot_id>", methods=["GET","POST"])
def book_slot(slot_id):
    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM slots WHERE id=?", (slot_id,))
    slot = cursor.fetchone()
    if not slot:
        conn.close()
        flash("Slot no encontrado.")
        return redirect(url_for("index"))

    if request.method == "POST":
        # datos del cliente
        client_name = request.form.get("name","").strip()
        client_email = request.form.get("email","").strip()
        notes = request.form.get("notes","").strip()
        scheduled_at = f"{slot['date']} {slot['time']}"

        # verificar disponibilidad a la hora de reservar (evitar race conditions simples)
        cursor.execute("SELECT available FROM slots WHERE id=?", (slot_id,))
        avail_row = cursor.fetchone()
        avail = avail_row["available"] if avail_row else 0
        if avail != 1:
            conn.close()
            flash("Lo sentimos, ese horario ya fue reservado por otra persona.")
            return redirect(url_for("index"))

        # insertar appointment
        cursor.execute("""
            INSERT INTO appointments (name, email, scheduled_at, notes, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (client_name, client_email, scheduled_at, notes, "scheduled", datetime.now().strftime("%Y-%m-%d %H:%M")))

        # marcar slot como no disponible
        cursor.execute("UPDATE slots SET available=0 WHERE id=?", (slot_id,))
        conn.commit()
        conn.close()

        # ====== Enviar correo al administrador (intentar) ======
        admin_msg = f"""
        <h2>Nueva cita reservada</h2>
        <p><b>Cliente:</b> {client_name}</p>
        <p><b>Email:</b> {client_email}</p>
        <p><b>Fecha:</b> {scheduled_at}</p>
        <p><b>Notas:</b> {notes}</p>
        """
        try:
            ok = send_email(to=ADMIN_EMAIL, subject="Nueva cita reservada", html_content=admin_msg)
            if ok:
                print("Notificación por email enviada al admin.")
            else:
                print("No se pudo enviar notificación por email (ver consola).")
        except Exception as e:
            print("Error al intentar enviar email:", e)

        flash("Cita reservada correctamente. Recibirás confirmación por correo si está configurado.")
        return redirect(url_for("index"))

    # GET -> mostrar formulario simple para reservar ese slot
    slot_info = {"id": slot["id"], "date": slot["date"], "time": slot["time"], "note": slot["note"]}
    conn.close()
    return render_template("book.html", slot=slot_info)

# ---------- ROUTES: Admin ----------
@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email","").strip()
        password = request.form.get("password","").strip()
        if email == ADMIN_EMAIL and password == ADMIN_PASSWORD:
            session["admin_logged"] = True
            session["admin_email"] = email
            flash("Has iniciado sesión correctamente.")
            next_url = request.args.get("next") or url_for("admin")
            return redirect(next_url)
        else:
            flash("Usuario o contraseña incorrectos.")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("Sesión cerrada.")
    return redirect(url_for("index"))

@app.route("/admin")
@login_required
def admin():
    conn = connect_db()
    c = conn.cursor()
    c.execute("SELECT * FROM appointments ORDER BY created_at DESC")
    appointments = c.fetchall()
    c.execute("SELECT * FROM slots ORDER BY date, time")
    slots = c.fetchall()
    conn.close()
    return render_template("admin.html", appointments=appointments, slots=slots)

# Admin: añadir nuevo slot
@app.route("/admin/slots/add", methods=["POST"])
@login_required
def admin_add_slot():
    date = request.form.get("date")
    time = request.form.get("time")
    note = request.form.get("note","")
    if not date or not time:
        flash("Fecha y hora son requeridos.")
        return redirect(url_for("admin"))
    conn = connect_db()
    c = conn.cursor()
    c.execute("INSERT INTO slots (date, time, available, note) VALUES (?, ?, 1, ?)", (date, time, note))
    conn.commit()
    conn.close()
    flash("Horario añadido.")
    return redirect(url_for("admin"))

# Admin: eliminar slot
@app.route("/admin/slots/delete/<int:slot_id>", methods=["POST"])
@login_required
def admin_delete_slot(slot_id):
    conn = connect_db()
    c = conn.cursor()
    c.execute("DELETE FROM slots WHERE id=?", (slot_id,))
    conn.commit()
    conn.close()
    flash("Horario eliminado.")
    return redirect(url_for("admin"))

# Admin: marcar slot libre/ocupado manualmente
@app.route("/admin/slots/toggle/<int:slot_id>", methods=["POST"])
@login_required
def admin_toggle_slot(slot_id):
    conn = connect_db()
    c = conn.cursor()
    # toggle available 0/1
    c.execute("SELECT available FROM slots WHERE id=?", (slot_id,))
    row = c.fetchone()
    if row:
        new = 0 if row["available"] == 1 else 1
        c.execute("UPDATE slots SET available=? WHERE id=?", (new, slot_id))
        conn.commit()
    conn.close()
    flash("Estado del horario actualizado.")
    return redirect(url_for("admin"))

# Admin: eliminar cita y liberar slot
@app.route("/admin/appointments/delete/<int:appointment_id>", methods=["POST"])
@login_required
def admin_delete_appointment(appointment_id):
    conn = connect_db()
    c = conn.cursor()

    # 1. obtener la cita
    c.execute("SELECT scheduled_at FROM appointments WHERE id=?", (appointment_id,))
    ap = c.fetchone()

    if ap:
        # scheduled_at esperable "YYYY-MM-DD HH:MM" o similar
        try:
            date, time = ap["scheduled_at"].split(" ")
        except Exception:
            # si no tiene espacio, no hacemos update de slot, solo borrar appointment
            date = None
            time = None

        # 2. liberar el slot correspondiente (si fecha y hora válidas)
        if date and time:
            c.execute("UPDATE slots SET available=1 WHERE date=? AND time=?", (date, time))

        # 3. eliminar la cita
        c.execute("DELETE FROM appointments WHERE id=?", (appointment_id,))

        conn.commit()

    conn.close()
    flash("La cita fue eliminada y el horario quedó libre nuevamente.")
    return redirect(url_for("admin"))

# Run
if __name__ == "__main__":
    app.run(debug=True)
