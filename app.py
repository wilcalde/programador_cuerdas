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
    
    # Ensure critical deniers exist in DB
    existing_names = {d['name'] for d in deniers}
    if "6000 expo" not in existing_names or "12000 expo" not in existing_names:
        try:
            for crit in ["6000 expo", "12000 expo"]:
                if crit not in existing_names:
                    db.create_denier(crit, 37.0)
            # Refresh list
            deniers = db.get_deniers()
        except:
            pass
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
    
    # Process "Automatic" requirements
    backlog_list = []
    for req in pending_requirements:
        backlog_list.append({
            'codigo': req['codigo'],
            'descripcion': req['descripcion'],
            'requerimientos': abs(req['requerimientos'] or 0),
            'prioridad': req.get('prioridad', False),
            'origen': 'Automatico'
        })
    
    # Process "Manual" requirements from orders
    for o in orders:
        if o.get('cabuya_codigo'):
            backlog_list.append({
                'codigo': o['cabuya_codigo'],
                'descripcion': '(Pedido Manual)',
                'requerimientos': o['total_kg'],
                'prioridad': True,
                'origen': 'Manual'
            })

    total_pending_kg = sum(req['requerimientos'] for req in backlog_list)
    
    return render_template('backlog.html', 
                         active_page='backlog', 
                         title='Backlog', 
                         orders=orders, 
                         deniers=deniers, 
                         backlog_list=backlog_list,
                         inventarios_cabuyas=inventarios_cabuyas,
                         total_pending_kg=total_pending_kg)

@app.route('/backlog/add', methods=['POST'])
def add_backlog():
    db = DBQueries()
    kg = request.form.get('kg', type=float)
    cabuya_codigo = request.form.get('cabuya_codigo')
    
    if cabuya_codigo and kg:
        # Auto-detect denier from product code
        cabuyas = db.get_inventarios_cabuyas()
        product = next((c for c in cabuyas if c['codigo'] == cabuya_codigo), None)
        
        if product:
            denier_name = product.get('referencia_denier')
            deniers = db.get_deniers()
            denier_obj = next((d for d in deniers if d['name'] == denier_name), None)
            
            if denier_obj:
                # Use today's date as default required date
                req_date = datetime.now().strftime('%Y-%m-%d')
                db.create_order(denier_obj['id'], kg, req_date, cabuya_codigo)
                flash(f"Pedido manual de {kg}kg para {cabuya_codigo} registrado", "success")
            else:
                flash(f"Error: No se encontró el Denier '{denier_name}' para el producto", "error")
        else:
            flash("Error: Código de producto no encontrado", "error")
            
    return redirect(url_for('backlog'))

@app.route('/backlog/edit', methods=['POST'])
def edit_backlog():
    db = DBQueries()
    order_id = request.form.get('order_id')
    denier_id = request.form.get('denier_id')
    kg = request.form.get('kg', type=float)
    req_date = request.form.get('required_date')
    cabuya_codigo = request.form.get('cabuya_codigo')
    
    if order_id and denier_id and kg and req_date:
        db.update_order(order_id, denier_id, kg, req_date, cabuya_codigo)
        flash(f"Pedido #{order_id[:6]} actualizado", "success")
    return redirect(url_for('backlog'))

@app.route('/backlog/delete/<order_id>', methods=['POST'])
def delete_backlog(order_id):
    db = DBQueries()
    db.delete_order(order_id)
    flash("Pedido eliminado", "success")
    return redirect(url_for('backlog'))

@app.route('/programming')
def programming():
    db = DBQueries()
    sc_data = db.get_all_scheduling_data()
    return render_template('programming.html', active_page='programming', title='Programación', sc_data=sc_data)

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
    
    # Pre-calculate next 30 days for shifts
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

@app.route('/ai')
def ai_consultancy():
    return render_template('ai.html', active_page='ai', title='Consultoría IA')

# Health check and Diagnostics
@app.route('/health')
def health():
    diagnostics = {
        "status": "online",
        "python": sys.version,
        "path": sys.path,
        "environment": {
            "SUPABASE_URL": "set" if os.environ.get("SUPABASE_URL") else "missing",
            "SUPABASE_KEY": "set" if os.environ.get("SUPABASE_KEY") else "missing"
        }
    }
    try:
        from db.queries import DBQueries
        db = DBQueries()
        db.get_deniers()
        diagnostics["database"] = "connected"
    except Exception as e:
        diagnostics["database_error"] = str(e)
        diagnostics["traceback"] = traceback.format_exc().split('\n')
    
    return jsonify(diagnostics)

@app.errorhandler(Exception)
def handle_exception(e):
    if hasattr(e, 'code') and isinstance(e.code, int) and e.code < 500:
        return jsonify(error=str(e)), e.code
    
    tb = traceback.format_exc()
    print(tb)
    return jsonify({
        "error": str(e),
        "traceback": tb.split('\n')
    }), 500

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

if __name__ == '__main__':
    app.run(debug=True)
