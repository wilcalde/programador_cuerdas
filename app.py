import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from datetime import datetime, timedelta
import json
import traceback
import sys
from db.queries import DBQueries

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "ciplas_master_cord_secret")

# Helper to check auth
def is_authenticated():
    return session.get('authenticated', False)

@app.before_request
def check_auth():
    if request.endpoint and 'static' not in request.endpoint and request.endpoint != 'login' and not is_authenticated():
        return redirect(url_for('login'))

@app.route('/')
def dashboard():
    from db.queries import DBQueries
    db = DBQueries()
    return render_template('dashboard.html', active_page='dashboard', title='Dashboard')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        # Simple simulation as per previous app logic
        if email == "admin@ciplas.com" and password == "admin123":
            session['authenticated'] = True
            session['user_email'] = email
            session['theme'] = 'dark'
            return redirect(url_for('dashboard'))
        else:
            flash("Credenciales incorrectas", "error")
            
    return render_template('login.html', title='Inicia Sesión')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/toggle-theme', methods=['POST'])
def toggle_theme():
    current_theme = session.get('theme', 'dark')
    session['theme'] = 'light' if current_theme == 'dark' else 'dark'
    return jsonify(success=True)

@app.route('/backlog')
def backlog():
    from db.queries import DBQueries
    db = DBQueries()
    orders = db.get_orders()
    deniers = db.get_deniers()
    
    # Sort deniers numerically by name, handling suffixes like 'expo'
    def denier_sort_key(d):
        name = d.get('name', '0')
        numeric_part = name.split(' ')[0]
        try:
            return (float(numeric_part), name)
        except ValueError:
            return (0.0, name)
            
    deniers.sort(key=denier_sort_key)
    
    pending_requirements = db.get_pending_requirements()
    inventarios_cabuyas = db.get_inventarios_cabuyas()
    
    return render_template('backlog.html', 
                         active_page='backlog', 
                         title='Backlog', 
                         orders=orders, 
                         deniers=deniers, 
                         pending_requirements=pending_requirements,
                         inventarios_cabuyas=inventarios_cabuyas)

@app.route('/config')
def config():
    from db.queries import DBQueries
    db = DBQueries()
    machines = db.get_machines_torsion()
    deniers = db.get_deniers()
    rewinder_configs = db.get_rewinder_denier_configs()
    machine_denier_configs = db.get_machine_denier_configs()
    inventarios_cabuyas = db.get_inventarios_cabuyas()
    
    # Group machine configs by machine_id
    machine_configs_mapped = {}
    for c in machine_denier_configs:
        m_id = c['machine_id']
        if m_id not in machine_configs_mapped:
            machine_configs_mapped[m_id] = {}
        machine_configs_mapped[m_id][str(c['denier'])] = c
    
    today = datetime.now().date()
    start_date = today + timedelta(days=1)
    end_date = start_date + timedelta(days=29)
    shifts_db = db.get_shifts(str(start_date), str(end_date))
    
    # Map shifts by date for easy lookup
    shifts_dict = {str(s['date']): s['working_hours'] for s in shifts_db}
    calendar = []
    curr = start_date
    while curr <= end_date:
        calendar.append({
            'date': str(curr),
            'display_date': curr.strftime('%d/%m'),
            'weekday': ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"][curr.weekday()],
            'hours': shifts_dict.get(str(curr), 24)
        })
        curr += timedelta(days=1)

    return render_template('config.html', 
                         active_page='config', 
                         title='Configuración',
                         machines=machines,
                         deniers=deniers,
                         machine_configs=machine_configs_mapped,
                         rewinder_configs={str(c['denier']): c for c in rewinder_configs},
                         calendar=calendar,
                         inventarios_cabuyas=inventarios_cabuyas)

@app.route('/config/cabuyas/update', methods=['POST'])
def update_cabuyas():
    db = DBQueries()
    try:
        updated_count = 0
        for key, value in request.form.items():
            if key.startswith('sec_'):
                codigo = key.replace('sec_', '')
                try:
                    security_val = float(value)
                    db.update_cabuya_inventory_security(codigo, security_val)
                    updated_count += 1
                except ValueError:
                    continue
        
        if updated_count > 0:
            flash(f"Se actualizaron {updated_count} niveles de seguridad.", "success")
        else:
            flash("No se realizaron cambios.", "info")
            
    except Exception as e:
        flash(f"Error al actualizar: {str(e)}", "error")
        
    return redirect(url_for('config'))

@app.route('/config/cabuyas/priority', methods=['POST'])
def update_cabuya_priority():
    db = DBQueries()
    data = request.json
    codigo = data.get('codigo')
    prioridad = data.get('prioridad')
    
    if codigo is not None:
        try:
            db.update_cabuya_priority(codigo, bool(prioridad))
            return jsonify(success=True)
        except Exception as e:
            return jsonify(success=False, error=str(e)), 500
    return jsonify(success=False, error="Missing data"), 400

@app.route('/reports')
def reports():
    return render_template('reports.html', active_page='reports', title='Reportes')

# Global error handler
@app.errorhandler(Exception)
def handle_exception(e):
    if hasattr(e, 'code') and isinstance(e.code, int) and e.code < 500:
        return jsonify(error=str(e)), e.code
    
    tb = traceback.format_exc()
    print(tb) # Will show in Vercel logs
    return jsonify({
        "error": str(e),
        "traceback": tb.split('\n')
    }), 500

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

if __name__ == '__main__':
    app.run(debug=True)
