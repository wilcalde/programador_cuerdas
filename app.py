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
    # Numeric sorting for deniers
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
    # We filter for orders created manually (usually have cabuya_codigo and weren't already accounted for)
    # For now, let's treat all pending orders as "Manual" items in this list if they have a code
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
                db.create_order(denier_obj['id'], kg, (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d'), cabuya_codigo)
                flash(f"Pedido añadido para {cabuya_codigo}", "success")
            else:
                flash(f"Error: Denier {denier_name} no encontrado en el catálogo", "error")
        else:
            flash("Error: Producto no encontrado", "error")
            
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
    
    data = request.json or {}
    strategy = data.get('strategy', 'kg')
    
    db = DBQueries()
    sc_data = db.get_all_scheduling_data()
    pending_requirements = db.get_pending_requirements()
    
    # Calculate backlog summary per Reference (Product/Cabuya)
    backlog_summary = {}
    
    # 1. Process Product Metadata for easy lookup
    all_products = db.get_inventarios_cabuyas()
    product_map = {p['codigo']: p for p in all_products}
    
    # 2. Process Manual Orders (Direct Demand)
    for o in sc_data['orders']:
        codigo = o.get('cabuya_codigo')
        if not codigo: continue
        
        prod = product_map.get(codigo)
        d_name = o.get('deniers', {}).get('name') if o.get('deniers') else (prod.get('referencia_denier') if prod else None)
        
        if not d_name: continue
        
        kg_pending = (o['total_kg'] - (o.get('produced_kg') or 0))
        if kg_pending <= 0.1: continue

        if codigo not in backlog_summary:
            backlog_summary[codigo] = {
                'description': prod.get('descripcion') if prod else 'Pedido Manual',
                'kg_total': 0, 
                'is_priority': True,
                'denier': d_name
            }
        backlog_summary[codigo]['kg_total'] += kg_pending

    # 3. Process Automatic Requirements (Only if NOT already covered by manual orders for the same code)
    # This prevents double counting if a manual order was created to cover a shortage.
    for req in pending_requirements:
        codigo = req['codigo']
        kg_req = abs(req['requerimientos'] or 0)
        if kg_req <= 0.1: continue

        if codigo in backlog_summary:
            # If manual order exists, we assume it's part of the requirement or an addition.
            # Usually, manual orders are specifically for a customer, while automatic are for stock.
            # To be safe and strictly follow "what is in backlog", we add them.
            backlog_summary[codigo]['kg_total'] += kg_req
            if req.get('prioridad'):
                backlog_summary[codigo]['is_priority'] = True
        else:
            prod = product_map.get(codigo)
            if not prod: continue
            d_name = prod.get('referencia_denier')
            if not d_name: continue
            
            backlog_summary[codigo] = {
                'description': prod.get('descripcion'),
                'kg_total': kg_req, 
                'is_priority': req.get('prioridad', False),
                'denier': d_name
            }

    result = generate_production_schedule(
        orders=sc_data['orders'],
        rewinder_capacities=sc_data['rewinder_capacities'],
        shifts=sc_data['shifts'],
        torsion_capacities=sc_data['torsion_capacities'],
        backlog_summary=backlog_summary,
        strategy=strategy
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

@app.route('/config/torsion/update', methods=['POST'])
def update_torsion():
    db = DBQueries()
    machine_id = request.form.get('machine_id')
    if not machine_id:
        flash("Error: No se especificó la máquina", "error")
        return redirect(url_for('config'))
    
    deniers = db.get_deniers()
    updated_count = 0
    for d in deniers:
        denier_name = d['name']
        denier_safe = denier_name.replace(' ', '_')
        rpm = request.form.get(f"rpm_{denier_safe}", type=int)
        torsiones = request.form.get(f"torsiones_{denier_safe}", type=int)
        husos = request.form.get(f"husos_{denier_safe}", type=int)
        
        if rpm is not None and torsiones is not None and husos is not None:
            db.upsert_machine_denier_config(machine_id, denier_name, rpm, torsiones, husos)
            updated_count += 1
    
    flash(f"✓ Configuración de {machine_id} actualizada ({updated_count} deniers)", "success")
    return redirect(url_for('config'))

@app.route('/config/rewinder/update', methods=['POST'])
def update_rewinder():
    db = DBQueries()
    deniers = db.get_deniers()
    updated_count = 0
    for d in deniers:
        denier_name = d['name']
        denier_safe = denier_name.replace(' ', '_')
        mp = request.form.get(f"mp_{denier_safe}", type=float)
        tm = request.form.get(f"tm_{denier_safe}", type=float)
        if mp is not None and tm is not None:
            db.upsert_rewinder_denier_config(denier_name, mp, tm)
            updated_count += 1
    flash(f"✓ Configuración Rewinder actualizada ({updated_count} deniers)", "success")
    return redirect(url_for('config', tab='rewinder'))

@app.route('/config/denier/add', methods=['POST'])
def add_denier():
    db = DBQueries()
    name = request.form.get('name')
    cycle = request.form.get('cycle', type=float)
    if name and cycle:
        db.create_denier(name, cycle)
        flash(f"Denier {name} añadido", "success")
    return redirect(url_for('config', tab='catalog'))

@app.route('/config/shifts/update', methods=['POST'])
def update_shifts():
    db = DBQueries()
    updated = 0
    for key, value in request.form.items():
        if key.startswith('shift_'):
            date_str = key.replace('shift_', '')
            db.upsert_shift(date_str, int(value))
            updated += 1
    flash(f"✓ Calendario actualizado ({updated} días)", "success")
    return redirect(url_for('config', tab='shifts'))

@app.route('/config/cabuyas/update', methods=['POST'])
def update_cabuyas():
    db = DBQueries()
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
        flash(f"✓ {updated_count} niveles de seguridad actualizados", "success")
    return redirect(url_for('config', tab='cabuyas'))

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
