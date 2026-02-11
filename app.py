from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
import os
from dotenv import load_dotenv
from db.queries import DBQueries
from db.client import get_supabase_client

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "ciplas-secret-key")

# --- Routes ---

@app.route('/')
def index():
    return render_template('dashboard.html')

@app.route('/deniers')
def deniers_page():
    db = DBQueries()
    deniers = db.get_deniers()
    return render_template('deniers.html', deniers=deniers)

@app.route('/machines')
def machines_page():
    db = DBQueries()
    machines = db.get_machines_torsion()
    return render_template('machines.html', machines=machines)

@app.route('/orders')
def orders_page():
    db = DBQueries()
    orders = db.get_orders()
    deniers = db.get_deniers()
    # Get products list for the dropdown
    products = db.get_inventarios_cabuyas()
    return render_template('orders.html', orders=orders, deniers=deniers, products=products)

@app.route('/reports')
def reports_page():
    db = DBQueries()
    machines = db.get_machines_torsion()
    return render_template('reports.html', machines=machines)

@app.route('/programming')
def programming_page():
    db = DBQueries()
    # No need to fetch all data here, API will handle it
    return render_template('programming.html')

@app.route('/config')
def config_page():
    db = DBQueries()
    # Get existing configurations
    torsion_configs = db.get_machine_denier_configs()
    rewinder_configs = db.get_rewinder_denier_configs()
    machines = db.get_machines_torsion()
    deniers = db.get_deniers()
    shifts = db.get_shifts()
    
    # NEW: Get products (cabuyas) inventory configuration
    inventarios_cabuyas = db.get_inventarios_cabuyas()
    
    return render_template('config.html', 
                         torsion_configs=torsion_configs, 
                         rewinder_configs=rewinder_configs,
                         machines=machines,
                         deniers=deniers,
                         shifts=shifts,
                         inventarios_cabuyas=inventarios_cabuyas)

@app.route('/backlog')
def backlog_page():
    db = DBQueries()
    sc_data = db.get_all_scheduling_data()
    pending_requirements = db.get_pending_requirements()
    
    # Calculate backlog summary per Reference (Product/Cabuya)
    backlog_summary = {}
    total_pending_kg = 0
    
    # 1. Process Product Metadata for easy lookup
    all_products = db.get_inventarios_cabuyas()
    product_map = {p['codigo']: p for p in all_products}
    
    # 2. Process Manual Orders
    for o in sc_data['orders']:
        codigo = o.get('cabuya_codigo')
        if not codigo: continue
        
        prod = product_map.get(codigo)
        if codigo not in backlog_summary:
            backlog_summary[codigo] = {
                'codigo': codigo,
                'descripcion': prod.get('descripcion') if prod else 'Pedido Manual',
                'requerimientos': 0, 
                'prioridad': True, # Manual orders are priority
                'origen': 'Manual'
            }
        
        kg_pending = (o['total_kg'] - (o.get('produced_kg') or 0))
        backlog_summary[codigo]['requerimientos'] += kg_pending
        total_pending_kg += kg_pending

    # 3. Process Automatic Requirements (Existencias vs Inv. Seguridad)
    for req in pending_requirements:
        codigo = req['codigo']
        if codigo not in backlog_summary:
            backlog_summary[codigo] = {
                'codigo': codigo,
                'descripcion': req.get('descripcion'),
                'requerimientos': 0, 
                'prioridad': req.get('prioridad', False),
                'origen': 'Automatico'
            }
        
        kg_req = abs(req['requerimientos'] or 0)
        backlog_summary[codigo]['requerimientos'] += kg_req
        total_pending_kg += kg_req

    # Convert to list for template
    backlog_list = list(backlog_summary.values())
    
    # Get all products for the addition form
    return render_template('backlog.html', 
                         backlog_list=backlog_list, 
                         total_pending_kg=total_pending_kg,
                         inventarios_cabuyas=all_products)

# --- API Endpoints ---

@app.route('/api/deniers', methods=['POST'])
def api_create_denier():
    db = DBQueries()
    name = request.form.get('name')
    cycle_time = float(request.form.get('cycle_time'))
    db.create_denier(name, cycle_time)
    return redirect(url_for('deniers_page'))

@app.route('/api/machines', methods=['POST'])
def api_update_machine():
    db = DBQueries()
    machine_id = request.form.get('machine_id')
    rpm = int(request.form.get('rpm'))
    torsions = int(request.form.get('torsions'))
    husos = int(request.form.get('husos'))
    db.update_machine_torsion(machine_id, rpm, torsions, husos)
    return redirect(url_for('machines_page'))

@app.route('/api/orders', methods=['POST'])
def api_create_order():
    db = DBQueries()
    denier_id = request.form.get('denier_id')
    kg = float(request.form.get('kg'))
    required_date = request.form.get('required_date')
    cabuya_codigo = request.form.get('cabuya_codigo')
    db.create_order(denier_id, kg, required_date, cabuya_codigo)
    return redirect(url_for('orders_page'))

@app.route('/api/orders/update', methods=['POST'])
def api_update_order():
    db = DBQueries()
    order_id = request.form.get('order_id')
    denier_id = request.form.get('denier_id')
    kg = float(request.form.get('kg'))
    required_date = request.form.get('required_date')
    cabuya_codigo = request.form.get('cabuya_codigo')
    db.update_order(order_id, denier_id, kg, required_date, cabuya_codigo)
    flash("Pedido actualizado correctamente", "success")
    return redirect(url_for('orders_page'))

@app.route('/api/orders/delete', methods=['POST'])
def api_delete_order():
    db = DBQueries()
    order_id = request.form.get('order_id')
    db.delete_order(order_id)
    flash("Pedido eliminado", "info")
    return redirect(url_for('orders_page'))

@app.route('/api/reports', methods=['POST'])
def api_create_report():
    db = DBQueries()
    machine_id = request.form.get('machine_id')
    report_type = request.form.get('type')
    description = request.form.get('description')
    impact_hours = float(request.form.get('impact_hours'))
    db.create_report(machine_id, report_type, description, impact_hours)
    return redirect(url_for('reports_page'))

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
    
    # 2. Process Manual Orders
    for o in sc_data['orders']:
        codigo = o.get('cabuya_codigo')
        if not codigo: continue
        
        prod = product_map.get(codigo)
        d_name = o.get('deniers', {}).get('name') if o.get('deniers') else (prod.get('referencia_denier') if prod else None)
        
        if not d_name: continue
        
        if codigo not in backlog_summary:
            backlog_summary[codigo] = {
                'description': prod.get('descripcion') if prod else 'Pedido Manual',
                'kg_total': 0, 
                'is_priority': True, # Manual orders are priority
                'denier': d_name
            }
        
        kg_pending = (o['total_kg'] - (o.get('produced_kg') or 0))
        backlog_summary[codigo]['kg_total'] += kg_pending

    # 3. Process Automatic Requirements (Existencias vs Inv. Seguridad)
    for req in pending_requirements:
        codigo = req['codigo']
        prod = product_map.get(codigo)
        if not prod: continue
        
        d_name = prod.get('referencia_denier')
        if not d_name: continue
        
        if codigo not in backlog_summary:
            backlog_summary[codigo] = {
                'description': prod.get('descripcion'),
                'kg_total': 0, 
                'is_priority': False,
                'denier': d_name
            }
        
        kg_req = abs(req['requerimientos'] or 0)
        backlog_summary[codigo]['kg_total'] += kg_req
        if req.get('prioridad'):
            backlog_summary[codigo]['is_priority'] = True

    result = generate_production_schedule(
        orders=sc_data['orders'],
        rewinder_capacities=sc_data['rewinder_capacities'],
        shifts=sc_data['shifts'],
        torsion_capacities=sc_data['torsion_capacities'],
        backlog_summary=backlog_summary,
        strategy=strategy
    )
    return jsonify(result)

@app.route('/api/save_schedule', methods=['POST'])
def api_save_schedule():
    db = DBQueries()
    data = request.json
    name = data.get('name', f"Plan {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    plan = data.get('plan')
    db.save_scheduling_scenario(name, plan)
    return jsonify({"success": True})

@app.route('/api/saved_schedules')
def api_get_saved_schedules():
    db = DBQueries()
    schedules = db.get_saved_schedules()
    return jsonify(schedules.data)

# --- Configuration Endpoints ---

@app.route('/config/torsion', methods=['POST'])
def api_config_torsion():
    db = DBQueries()
    machine_id = request.form.get('machine_id')
    denier = request.form.get('denier')
    rpm = int(request.form.get('rpm'))
    torsions = int(request.form.get('torsions'))
    husos = int(request.form.get('husos'))
    db.upsert_machine_denier_config(machine_id, denier, rpm, torsions, husos)
    flash(f"Configuración guardada para {machine_id}", "success")
    return redirect(url_for('config_page'))

@app.route('/config/rewinder', methods=['POST'])
def api_config_rewinder():
    db = DBQueries()
    denier = request.form.get('denier')
    mp_seg = float(request.form.get('mp_segundos'))
    tm_min = float(request.form.get('tm_minutos'))
    db.upsert_rewinder_denier_config(denier, mp_seg, tm_min)
    flash(f"Configuración guardada para denier {denier}", "success")
    return redirect(url_for('config_page'))

@app.route('/config/shifts', methods=['POST'])
def api_config_shifts():
    db = DBQueries()
    date = request.form.get('date')
    hours = int(request.form.get('working_hours'))
    db.upsert_shift(date, hours)
    flash(f"Turno actualizado para {date}", "success")
    return redirect(url_for('config_page'))

@app.route('/config/cabuyas/priority', methods=['POST'])
def api_update_cabuya_priority():
    db = DBQueries()
    data = request.json
    codigo = data.get('codigo')
    prioridad = data.get('prioridad')
    db.update_cabuya_priority(codigo, prioridad)
    return jsonify({"success": True})

@app.route('/config/cabuyas/security', methods=['POST'])
def api_update_cabuya_security():
    db = DBQueries()
    data = request.json
    codigo = data.get('codigo')
    security_value = float(data.get('security_value'))
    db.update_cabuya_inventory_security(codigo, security_value)
    return jsonify({"success": True})

@app.route('/backlog/add', methods=['POST'])
def api_add_manual_backlog():
    db = DBQueries()
    codigo = request.form.get('cabuya_codigo')
    kg = float(request.form.get('kg'))
    
    # Check if product exists to get denier
    all_products = db.get_inventarios_cabuyas()
    prod = next((p for p in all_products if p['codigo'] == codigo), None)
    
    if not prod:
        flash("Producto no encontrado", "error")
        return redirect(url_for('backlog_page'))
    
    # Create an order entry to track this manual request
    db.create_order(
        denier_id=None, # We'll use cabuya_codigo to link
        kg=kg,
        required_date=datetime.now().strftime('%Y-%m-%d'),
        cabuya_codigo=codigo
    )
    
    flash(f"Pedido manual de {kg}kg para {codigo} añadido", "success")
    return redirect(url_for('backlog_page'))

# --- AI Consultant Endpoint ---

@app.route('/api/ai_consultant', methods=['POST'])
def api_ai_consultant():
    from integrations.openai_ia import get_ai_optimization_scenario
    db = DBQueries()
    
    # Get backlog summary
    sc_data = db.get_all_scheduling_data()
    # Simplified backlog for context
    backlog_simple = []
    for o in sc_data['orders']:
        kg = o['total_kg'] - (o.get('produced_kg') or 0)
        if kg > 0:
            backlog_simple.append({"ref": o.get('deniers', {}).get('name'), "kg": kg})
            
    reports = db.supabase.table("reports").select("*").limit(5).execute().data
    
    advice = get_ai_optimization_scenario(backlog_simple, reports)
    return jsonify({"advice": advice})

# --- Vercel specific ---
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
