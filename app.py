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
    
    return render_template('backlog.html', 
                         active_page='backlog', 
                         title='Backlog', 
                         orders=orders, 
                         deniers=deniers, 
                         pending_requirements=pending_requirements,
                         inventarios_cabuyas=inventarios_cabuyas)

@app.route('/backlog/add', methods=['POST'])
def add_backlog():
    db = DBQueries()
    denier_id = request.form.get('denier_id')
    kg = request.form.get('kg', type=float)
    req_date = request.form.get('required_date')
    cabuya_codigo = request.form.get('cabuya_codigo')
    
    if denier_id and kg and req_date:
        db.create_order(denier_id, kg, req_date, cabuya_codigo)
        flash(f"Pedido de {kg}kg guardado", "success")
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

@app.route('/api/generate_schedule', methods=['POST'])
def api_generate_schedule():
    from db.queries import DBQueries
    from integrations.openai_ia import generate_production_schedule
    db = DBQueries()
    sc_data = db.get_all_scheduling_data()
    
    # Calculate backlog summary per denier
    backlog_summary = {}
    for o in sc_data['orders']:
        d_name = o.get('deniers', {}).get('name')
        if not d_name: continue
        if d_name not in backlog_summary:
            backlog_summary[d_name] = {'kg_total': 0}
        backlog_summary[d_name]['kg_total'] += (o['total_kg'] - (o.get('produced_kg') or 0))

    result = generate_production_schedule(
        orders=sc_data['orders'],
        rewinder_capacities=sc_data['rewinder_capacities'],
        shifts=sc_data['shifts'],
        torsion_capacities=sc_data['torsion_capacities'],
        backlog_summary=backlog_summary
    )
    
    return jsonify(result)

@app.route('/api/ai_chat', methods=['POST'])
def api_ai_chat():
    data = request.json
    user_message = data.get('message')
    from db.queries import DBQueries
    db = DBQueries()
    orders = db.get_orders()
    
    # Simple context injection
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": f"Eres el asistente inteligente de la planta Ciplas. Tienes acceso al backlog actual: {orders}. Responde de forma profesional y técnica."},
                {"role": "user", "content": user_message}
            ]
        )
        return jsonify({"response": response.choices[0].message.content})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route('/api/ai_scenario', methods=['POST'])
def api_ai_scenario():
    from db.queries import DBQueries
    from integrations.openai_ia import get_ai_optimization_scenario
    db = DBQueries()
    orders = db.get_orders()
    # Mocking reports for now or fetching from DB if available
    reports = [] 
    scenario = get_ai_optimization_scenario(orders, reports)
    return jsonify({"response": scenario})

@app.route('/api/save_schedule', methods=['POST'])
def api_save_schedule():
    data = request.json
    name = data.get('name', 'Programación IA')
    plan = data.get('plan')
    
    if not plan:
        return jsonify({"error": "No hay plan para guardar"}), 400
        
    db = DBQueries()
    try:
        db.save_scheduling_scenario(name, plan)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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
    
    # Pre-calculate next 15 days for shifts
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

@app.route('/config/torsion/update', methods=['POST'])
def update_torsion():
    db = DBQueries()
    machine_id = request.form.get('machine_id')
    
    if not machine_id:
        flash("Error: No se especificó la máquina", "error")
        return redirect(url_for('config'))
    
    # Fetch deniers from DB to iterate dynamically
    deniers = db.get_deniers()
    updated_deniers = []
    errors = []
    
    for d in deniers:
        denier_name = d['name']
        denier_safe = denier_name.replace(' ', '_')
        
        rpm = request.form.get(f"rpm_{denier_safe}", type=int)
        torsiones = request.form.get(f"torsiones_{denier_safe}", type=int)
        husos = request.form.get(f"husos_{denier_safe}", type=int)
        
        # Only save if all three values are provided (not None)
        if rpm is not None and torsiones is not None and husos is not None:
            try:
                db.upsert_machine_denier_config(machine_id, denier_name, rpm, torsiones, husos)
                updated_deniers.append(denier_name)
            except Exception as e:
                errors.append(f"Error guardando denier {denier_name}: {str(e)}")
    
    # Provide detailed feedback
    if errors:
        for error in errors:
            flash(error, "error")
    
    if updated_deniers:
        flash(f"✓ Configuración de {machine_id} actualizada para: {', '.join(updated_deniers)}", "success")
    else:
        flash(f"No se actualizó ninguna configuración para {machine_id}", "warning")
    
    return redirect(url_for('config'))

@app.route('/config/rewinder/update', methods=['POST'])
def update_rewinder():
    db = DBQueries()
    deniers = db.get_deniers()
    for d in deniers:
        denier_name = d['name']
        denier_safe = denier_name.replace(' ', '_')
        mp = request.form.get(f"mp_{denier_safe}", type=float)
        tm = request.form.get(f"tm_{denier_safe}", type=float)
        if mp is not None and tm is not None:
            db.upsert_rewinder_denier_config(denier_name, mp, tm)
    flash("Configuración Rewinder actualizada", "success")
    return redirect(url_for('config'))

@app.route('/config/shifts/update', methods=['POST'])
def update_shifts():
    db = DBQueries()
    # Get all shift dates from form keys
    for key, value in request.form.items():
        if key.startswith('shift_'):
            date_str = key.replace('shift_', '')
            db.upsert_shift(date_str, int(value))
    flash("Calendario de turnos actualizado", "success")
    return redirect(url_for('config'))

@app.route('/config/denier/add', methods=['POST'])
def add_denier():
    db = DBQueries()
    name = request.form.get('name')
    cycle = request.form.get('cycle', type=float)
    if name and cycle:
        db.create_denier(name, cycle)
        flash(f"Denier {name} añadido", "success")
    return redirect(url_for('config'))

@app.route('/config/cabuyas/update', methods=['POST'])
def update_cabuyas():
    db = DBQueries()
    try:
        # Expecting a list of updates or iterating through form
        # To keep it simple, we'll iterate through all inputs starting with 'sec_'
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

# Global error handler to catch and show 500 details
@app.errorhandler(Exception)
def handle_exception(e):
    # Pass through HTTP errors
    if hasattr(e, 'code') and isinstance(e.code, int) and e.code < 500:
        return jsonify(error=str(e)), e.code
    
    tb = traceback.format_exc()
    print(tb) # Will show in Vercel logs
    return jsonify({
        "error": str(e),
        "traceback": tb.split('\n')
    }), 500

# Error handler for 404
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

if __name__ == '__main__':
    app.run(debug=True)
