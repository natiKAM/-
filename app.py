from flask import Flask, render_template, request, jsonify, session, make_response
from models import db, Employee, Client, WorkLog
from datetime import datetime, timedelta
import csv
import io
import os

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'change-this-secret-key-in-production')

# Support both local SQLite and cloud PostgreSQL
_db_url = os.environ.get('DATABASE_URL', 'sqlite:///worktracker.db')
if _db_url.startswith('postgres://'):  # Railway uses postgres://, SQLAlchemy needs postgresql://
    _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = _db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

# Initialize DB on startup (works with both gunicorn and direct run)
with app.app_context():
    db.create_all()
    # Create default admin if not exists
    if not Employee.query.filter_by(is_admin=True).first():
        admin = Employee(name='Admin', pin='1234', is_admin=True)
        db.session.add(admin)
    if not Client.query.first():
        for name in ['לקוח לדוגמה א', 'לקוח לדוגמה ב']:
            db.session.add(Client(name=name))
    db.session.commit()


def init_db():
    with app.app_context():
        db.create_all()
        if not Employee.query.filter_by(is_admin=True).first():
            admin = Employee(name='Admin', pin='1234', is_admin=True)
            db.session.add(admin)
        if not Client.query.first():
            for name in ['לקוח לדוגמה א', 'לקוח לדוגמה ב']:
                db.session.add(Client(name=name))
        db.session.commit()


# ── Pages ──────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/admin')
def admin():
    return render_template('admin.html')


# ── Auth ───────────────────────────────────────────────────────────────────────

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    emp_id = data.get('employee_id')
    pin = data.get('pin', '').strip()
    emp = Employee.query.filter_by(id=emp_id, pin=pin).first()
    if not emp:
        return jsonify({'error': 'פרטים שגויים. נסה שוב.'}), 401
    session['employee_id'] = emp.id
    session['is_admin'] = emp.is_admin
    session['name'] = emp.name
    return jsonify({'success': True, 'name': emp.name, 'is_admin': emp.is_admin})


@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})


@app.route('/api/me', methods=['GET'])
def me():
    if 'employee_id' not in session:
        return jsonify({'logged_in': False})
    return jsonify({
        'logged_in': True,
        'employee_id': session['employee_id'],
        'name': session.get('name'),
        'is_admin': session.get('is_admin', False)
    })


# ── Public lists ───────────────────────────────────────────────────────────────

@app.route('/api/employees', methods=['GET'])
def get_employees():
    emps = Employee.query.filter_by(is_admin=False).order_by(Employee.name).all()
    return jsonify([{'id': e.id, 'name': e.name} for e in emps])


@app.route('/api/clients', methods=['GET'])
def get_clients():
    clients = Client.query.filter_by(is_active=True).order_by(Client.name).all()
    return jsonify([{'id': c.id, 'name': c.name} for c in clients])


# ── Timer ──────────────────────────────────────────────────────────────────────

@app.route('/api/timer/start', methods=['POST'])
def start_timer():
    if 'employee_id' not in session:
        return jsonify({'error': 'לא מחובר'}), 401
    data = request.json
    client_id = data.get('client_id')
    description = data.get('description', '').strip()
    if not client_id:
        return jsonify({'error': 'יש לבחור לקוח'}), 400
    active = WorkLog.query.filter_by(employee_id=session['employee_id'], is_running=True).first()
    if active:
        return jsonify({'error': 'יש טיימר פעיל. סיים אותו קודם.'}), 400
    log = WorkLog(
        employee_id=session['employee_id'],
        client_id=client_id,
        description=description,
        start_time=datetime.now(),
        is_running=True
    )
    db.session.add(log)
    db.session.commit()
    return jsonify({'success': True, 'log_id': log.id, 'start_time': log.start_time.isoformat()})


@app.route('/api/timer/stop', methods=['POST'])
def stop_timer():
    if 'employee_id' not in session:
        return jsonify({'error': 'לא מחובר'}), 401
    log = WorkLog.query.filter_by(employee_id=session['employee_id'], is_running=True).first()
    if not log:
        return jsonify({'error': 'אין טיימר פעיל'}), 404
    log.end_time = datetime.now()
    log.duration_minutes = max(1, int((log.end_time - log.start_time).total_seconds() / 60))
    log.is_running = False
    db.session.commit()
    return jsonify({'success': True, 'duration_minutes': log.duration_minutes})


@app.route('/api/timer/status', methods=['GET'])
def timer_status():
    if 'employee_id' not in session:
        return jsonify({'running': False})
    log = WorkLog.query.filter_by(employee_id=session['employee_id'], is_running=True).first()
    if log:
        elapsed = int((datetime.now() - log.start_time).total_seconds())
        return jsonify({
            'running': True,
            'start_time': log.start_time.isoformat(),
            'elapsed_seconds': elapsed,
            'client_id': log.client_id,
            'description': log.description
        })
    return jsonify({'running': False})


# ── Manual log ─────────────────────────────────────────────────────────────────

@app.route('/api/logs/manual', methods=['POST'])
def add_manual_log():
    if 'employee_id' not in session:
        return jsonify({'error': 'לא מחובר'}), 401
    data = request.json
    date = data.get('date', '').strip()
    start_str = data.get('start_time', '').strip()
    end_str = data.get('end_time', '').strip()
    client_id = data.get('client_id')
    description = data.get('description', '').strip()
    if not all([date, start_str, end_str, client_id]):
        return jsonify({'error': 'יש למלא את כל השדות'}), 400
    try:
        start_dt = datetime.strptime(f"{date} {start_str}", '%Y-%m-%d %H:%M')
        end_dt = datetime.strptime(f"{date} {end_str}", '%Y-%m-%d %H:%M')
    except ValueError:
        return jsonify({'error': 'פורמט תאריך/שעה שגוי'}), 400
    if end_dt <= start_dt:
        return jsonify({'error': 'שעת סיום חייבת להיות אחרי שעת התחלה'}), 400
    duration = max(1, int((end_dt - start_dt).total_seconds() / 60))
    log = WorkLog(
        employee_id=session['employee_id'],
        client_id=client_id,
        description=description,
        start_time=start_dt,
        end_time=end_dt,
        duration_minutes=duration,
        is_running=False
    )
    db.session.add(log)
    db.session.commit()
    return jsonify({'success': True})


# ── Employee's own logs ────────────────────────────────────────────────────────

@app.route('/api/my/logs', methods=['GET'])
def my_logs():
    if 'employee_id' not in session:
        return jsonify({'error': 'לא מחובר'}), 401
    logs = (WorkLog.query
            .filter_by(employee_id=session['employee_id'], is_running=False)
            .order_by(WorkLog.start_time.desc())
            .limit(30).all())
    return jsonify([_format_log(l) for l in logs])


# ── Admin: Reports ─────────────────────────────────────────────────────────────

def _require_admin():
    if not session.get('is_admin'):
        return jsonify({'error': 'אין הרשאת גישה'}), 403
    return None


def _build_log_query():
    q = WorkLog.query.filter_by(is_running=False)
    emp_id = request.args.get('employee_id')
    client_id = request.args.get('client_id')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    if emp_id:
        q = q.filter(WorkLog.employee_id == int(emp_id))
    if client_id:
        q = q.filter(WorkLog.client_id == int(client_id))
    if date_from:
        q = q.filter(WorkLog.start_time >= datetime.strptime(date_from, '%Y-%m-%d'))
    if date_to:
        q = q.filter(WorkLog.start_time < datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1))
    return q


def _format_log(log):
    return {
        'id': log.id,
        'employee': log.employee.name,
        'client': log.client.name if log.client else '—',
        'description': log.description or '',
        'date': log.start_time.strftime('%d/%m/%Y'),
        'start_time': log.start_time.strftime('%H:%M'),
        'end_time': log.end_time.strftime('%H:%M') if log.end_time else '—',
        'duration_minutes': log.duration_minutes or 0
    }


@app.route('/api/admin/logs', methods=['GET'])
def admin_get_logs():
    err = _require_admin()
    if err:
        return err
    logs = _build_log_query().order_by(WorkLog.start_time.desc()).all()
    data = [_format_log(l) for l in logs]
    total = sum(d['duration_minutes'] for d in data)
    return jsonify({'logs': data, 'total_minutes': total})


@app.route('/api/admin/logs/<int:log_id>', methods=['DELETE'])
def admin_delete_log(log_id):
    err = _require_admin()
    if err:
        return err
    log = WorkLog.query.get_or_404(log_id)
    db.session.delete(log)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/admin/logs/export', methods=['GET'])
def export_csv():
    err = _require_admin()
    if err:
        return err
    logs = _build_log_query().order_by(WorkLog.start_time.desc()).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['עובד', 'לקוח', 'תיאור', 'תאריך', 'שעת התחלה', 'שעת סיום', 'משך (דקות)', 'משך (שעות)'])
    for log in logs:
        mins = log.duration_minutes or 0
        writer.writerow([
            log.employee.name,
            log.client.name if log.client else '',
            log.description or '',
            log.start_time.strftime('%d/%m/%Y'),
            log.start_time.strftime('%H:%M'),
            log.end_time.strftime('%H:%M') if log.end_time else '',
            mins,
            f"{mins / 60:.2f}"
        ])
    response = make_response('\ufeff' + output.getvalue())
    response.headers['Content-Disposition'] = 'attachment; filename=work_logs.csv'
    response.headers['Content-Type'] = 'text/csv; charset=utf-8-sig'
    return response


# ── Admin: Employees CRUD ──────────────────────────────────────────────────────

@app.route('/api/admin/employees', methods=['GET'])
def admin_get_employees():
    err = _require_admin()
    if err:
        return err
    emps = Employee.query.filter_by(is_admin=False).order_by(Employee.name).all()
    return jsonify([{'id': e.id, 'name': e.name, 'pin': e.pin} for e in emps])


@app.route('/api/admin/employees', methods=['POST'])
def admin_add_employee():
    err = _require_admin()
    if err:
        return err
    data = request.json
    name = data.get('name', '').strip()
    pin = data.get('pin', '').strip()
    if not name or not pin:
        return jsonify({'error': 'שם וקוד PIN הם שדות חובה'}), 400
    if len(pin) != 4 or not pin.isdigit():
        return jsonify({'error': 'PIN חייב להיות 4 ספרות'}), 400
    e = Employee(name=name, pin=pin)
    db.session.add(e)
    db.session.commit()
    return jsonify({'success': True, 'id': e.id})


@app.route('/api/admin/employees/<int:emp_id>', methods=['PUT'])
def admin_update_employee(emp_id):
    err = _require_admin()
    if err:
        return err
    e = Employee.query.get_or_404(emp_id)
    data = request.json
    name = data.get('name', '').strip()
    pin = data.get('pin', '').strip()
    if name:
        e.name = name
    if pin:
        if len(pin) != 4 or not pin.isdigit():
            return jsonify({'error': 'PIN חייב להיות 4 ספרות'}), 400
        e.pin = pin
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/admin/employees/<int:emp_id>', methods=['DELETE'])
def admin_delete_employee(emp_id):
    err = _require_admin()
    if err:
        return err
    e = Employee.query.get_or_404(emp_id)
    db.session.delete(e)
    db.session.commit()
    return jsonify({'success': True})


# ── Admin: Clients CRUD ────────────────────────────────────────────────────────

@app.route('/api/admin/clients', methods=['GET'])
def admin_get_clients():
    err = _require_admin()
    if err:
        return err
    clients = Client.query.order_by(Client.name).all()
    return jsonify([{'id': c.id, 'name': c.name, 'is_active': c.is_active} for c in clients])


@app.route('/api/admin/clients', methods=['POST'])
def admin_add_client():
    err = _require_admin()
    if err:
        return err
    data = request.json
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'error': 'שם לקוח הוא שדה חובה'}), 400
    c = Client(name=name)
    db.session.add(c)
    db.session.commit()
    return jsonify({'success': True, 'id': c.id})


@app.route('/api/admin/clients/<int:client_id>', methods=['PUT'])
def admin_update_client(client_id):
    err = _require_admin()
    if err:
        return err
    c = Client.query.get_or_404(client_id)
    data = request.json
    if 'name' in data and data['name'].strip():
        c.name = data['name'].strip()
    if 'is_active' in data:
        c.is_active = data['is_active']
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/admin/clients/<int:client_id>', methods=['DELETE'])
def admin_delete_client(client_id):
    err = _require_admin()
    if err:
        return err
    c = Client.query.get_or_404(client_id)
    c.is_active = False  # soft delete to preserve log history
    db.session.commit()
    return jsonify({'success': True})


if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
