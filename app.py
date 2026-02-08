import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from db.queries import DBQueries
from integrations.openai_ia import generate_production_schedule, get_ai_optimization_scenario
import pandas as pd
import json

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
    db = DBQueries()
    orders = db.get_orders()
    return render_template('backlog.html', active_page='backlog', title='Backlog', orders=orders)

@app.route('/backlog/add', methods=['POST'])
def add_backlog():
    db = DBQueries()
    denier = request.form.get('denier')
    kg = request.form.get('kg', type=float)
    req_date = request.form.get('required_date')
    
    # Get denier_id from name
    deniers = db.get_deniers()
    denier_id = next((d['id'] for d in deniers if d['name'] == denier), None)
    
    if denier_id and kg and req_date:
        db.create_order(denier_id, kg, req_date)
        flash(f"Pedido de {kg}kg para Denier {denier} guardado", "success")
    return redirect(url_for('backlog'))

@app.route('/programming')
def programming():
    db = DBQueries()
    sc_data = db.get_all_scheduling_data()
    return render_template('programming.html', active_page='programming', title='Programación', sc_data=sc_data)

@app.route('/api/generate_schedule', methods=['POST'])
def api_generate_schedule():
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
    db = DBQueries()
    orders = db.get_orders()
    
    # Simple context injection
    from integrations.openai_ia import OpenAI
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
    db = DBQueries()
    machines = db.get_machines_torsion()
    deniers = db.get_deniers()
    rewinder_configs = db.get_rewinder_denier_configs()
    machine_denier_configs = db.get_machine_denier_configs()
    
    # Group machine configs by machine_id
    machine_configs_mapped = {}
    for c in machine_denier_configs:
        m_id = c['machine_id']
        if m_id not in machine_configs_mapped:
            machine_configs_mapped[m_id] = {}
        machine_configs_mapped[m_id][str(c['denier'])] = c
    
    # Pre-calculate next 15 days for shifts
    today = pd.Timestamp.now().date()
    start_date = today + pd.Timedelta(days=1)
    end_date = start_date + pd.Timedelta(days=14)
    shifts = db.get_shifts(str(start_date), str(end_date))
    
    # Map shifts by date for easy lookup
    shifts_dict = {str(s['date']): s['working_hours'] for s in shifts}
    calendar = []
    curr = start_date
    while curr <= end_date:
        calendar.append({
            'date': str(curr),
            'display_date': curr.strftime('%d/%m'),
            'weekday': ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"][curr.weekday()],
            'hours': shifts_dict.get(str(curr), 24)
        })
        curr += pd.Timedelta(days=1)

    return render_template('config.html', 
                         active_page='config', 
                         title='Configuración',
                         machines=machines,
                         deniers=deniers,
                         machine_configs=machine_configs_mapped,
                         rewinder_configs={str(c['denier']): c for c in rewinder_configs},
                         calendar=calendar)

@app.route('/config/torsion/update', methods=['POST'])
def update_torsion():
    db = DBQueries()
    machine_id = request.form.get('machine_id')
    # Fetch all denier configs from form
    denier_options = ["2000", "2500", "3000", "4000", "6000", "9000", "12000", "18000"]
    for denier in denier_options:
        rpm = request.form.get(f"rpm_{denier}", type=int)
        torsiones = request.form.get(f"torsiones_{denier}", type=int)
        husos = request.form.get(f"husos_{denier}", type=int)
        if rpm and torsiones and husos:
            db.upsert_machine_denier_config(machine_id, denier, rpm, torsiones, husos)
    flash(f"Configuración de {machine_id} actualizada", "success")
    return redirect(url_for('config'))

@app.route('/config/rewinder/update', methods=['POST'])
def update_rewinder():
    db = DBQueries()
    denier_options = ["2000", "2500", "3000", "4000", "6000", "9000", "12000", "18000"]
    for denier in denier_options:
        mp = request.form.get(f"mp_{denier}", type=float)
        tm = request.form.get(f"tm_{denier}", type=float)
        if mp is not None and tm is not None:
            db.upsert_rewinder_denier_config(denier, mp, tm)
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

@app.route('/reports')
def reports():
    return render_template('reports.html', active_page='reports', title='Reportes')

@app.route('/ai')
def ai_consultancy():
    return render_template('ai.html', active_page='ai', title='Consultoría IA')

# Error handler for 404
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

if __name__ == '__main__':
    app.run(debug=True)
