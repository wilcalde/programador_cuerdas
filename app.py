import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from datetime import datetime, timedelta
import json
import traceback
import re
import sys
from db.queries import DBQueries

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "ciplas_master_cord_secret")

# Helper to check auth
def is_authenticated():
    return session.get('authenticated', False)

def infer_denier_from_description(descripcion):
    """Infer denier value from product description when denier column is null.
    E.g. 'CABUYA ECO 12x1K VERDE' -> '12000', 'CABUYA CLA 9X1' -> '9000'
    """
    if not descripcion:
        return None
    match = re.search(r'(\d+)\s*[xX]\s*1', descripcion)
    if match:
        multiplier = int(match.group(1))
        return str(multiplier * 1000)
    return None

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
            deniers = db.get_deniers()
        except:
            pass
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
    rewinder_configs = db.get_rewinder_denier_configs()
    
    # Calculate Kg/h for each denier in rewinder config
    kgh_map = {}
    for cfg in rewinder_configs:
        tm_min = cfg.get('tm_minutos', 0)
        if tm_min > 0:
            kgh_map[str(cfg['denier'])] = (60 / tm_min) * 0.8
        else:
            kgh_map[str(cfg['denier'])] = 0

    # Build cabuya lookup for manual orders
    cabuya_lookup = {c['codigo']: c for c in inventarios_cabuyas}
    
    # Process "Automatic" requirements
    backlog_list = []
    for req in pending_requirements:
        kg_req = abs(req['requerimientos'] or 0)
        # Determine denier name
        d_val = req.get('denier')
        if d_val is not None:
            d_name = str(int(d_val)) if isinstance(d_val, (int, float)) else str(d_val)
        else:
            d_name = infer_denier_from_description(req.get('descripcion'))
        
        # Calculate h_proceso
        kgh = kgh_map.get(d_name, 0)
        h_proceso = kg_req / kgh if kgh > 0 else 0
        
        backlog_list.append({
            'codigo': req['codigo'],
            'descripcion': req['descripcion'],
            'requerimientos': kg_req,
            'prioridad': req.get('prioridad', False),
            'origen': 'Automatico',
            'h_proceso': h_proceso
        })
    
    # Process "Manual" requirements from orders
    for o in orders:
        if o.get('cabuya_codigo'):
            kg_pending = o['total_kg']
            codigo = o['cabuya_codigo']
            
            # Lookup denier from cabuya info
            cabuya_lookup_val = cabuya_lookup.get(codigo, {})
            d_val = cabuya_lookup_val.get('denier')
            if d_val is not None:
                d_name = str(int(d_val)) if isinstance(d_val, (int, float)) else str(d_val)
            else:
                d_name = infer_denier_from_description(cabuya_lookup_val.get('descripcion'))
            
            # Calculate h_proceso
            kgh = kgh_map.get(d_name, 0)
            h_proceso = kg_pending / kgh if kgh > 0 else 0

            backlog_list.append({
                'codigo': codigo,
                'descripcion': '(Pedido Manual)',
                'requerimientos': kg_pending,
                'prioridad': True,
                'origen': 'Manual',
                'h_proceso': h_proceso
            })

    total_pending_kg = sum(req['requerimientos'] for req in backlog_list)
    total_h_proceso = sum(req['h_proceso'] for req in backlog_list)
    
    return render_template('backlog.html', 
                         active_page='backlog', 
                         title='Backlog', 
                         orders=orders, 
                         deniers=deniers, 
                         backlog_list=backlog_list,
                         inventarios_cabuyas=inventarios_cabuyas,
                         total_pending_kg=total_pending_kg,
                         total_h_proceso=total_h_proceso)

@app.route('/backlog/add', methods=['POST'])
def add_backlog():
    db = DBQueries()
    kg = request.form.get('kg', type=float)
    cabuya_codigo = request.form.get('cabuya_codigo')
    
    if cabuya_codigo and kg:
        cabuyas = db.get_inventarios_cabuyas()
        product = next((c for c in cabuyas if c['codigo'] == cabuya_codigo), None)
        
        if product:
            denier_val = product.get('denier')
            if denier_val:
                # Handle alphanumeric deniers like \"12000 EXPO\"
                if isinstance(denier_val, (int, float)):
                    denier_name = str(int(denier_val))
                else:
                    denier_name = str(denier_val)
            else:
                denier_name = infer_denier_from_description(product.get('descripcion'))
            
            if denier_name:
                deniers = db.get_deniers()
                denier_obj = next((d for d in deniers if d['name'] == denier_name), None)
                
                if denier_obj:
                    req_date = datetime.now().strftime('%Y-%m-%d')
                    db.create_order(denier_obj['id'], kg, req_date, cabuya_codigo)
                    flash(f\"Pedido manual de {kg}kg para {cabuya_codigo} registrado\", \"success\")
                else:
                    flash(f\"Error: No se encontró el Denier '{denier_name}' para el producto\", \"error\")
            else:
                flash(\"Error: No se pudo determinar el Denier del producto\", \"error\")
        else:
            flash(\"Error: Código de producto no encontrado\", \"error\")
            
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
        flash(f\"Pedido #{order_id[:6]} actualizado\", \"success\")
    return redirect(url_for('backlog'))

@app.route('/backlog/delete/<order_id>', methods=['POST'])
def delete_backlog(order_id):
    db = DBQueries()
    db.delete_order(order_id)
    flash(\"Pedido eliminado\", \"success\")
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
    
    # ============================================================
    # BUILD BACKLOG SUMMARY DIRECTLY FROM PENDING REQUIREMENTS
    # This is the ONLY source of truth (matches exactly what backlog.html shows)
    # ============================================================
    backlog_summary = {}
    
    # The pending_requirements come directly from inventarios_cabuyas where requerimientos < 0
    # Each record has: codigo, descripcion, denier (float or null), requerimientos (negative), prioridad
    for req in pending_requirements:
        codigo = req['codigo']
        kg_req = abs(req['requerimientos'] or 0)
        if kg_req <= 0.1:
            continue
        
        # Get denier name for this product
        # Column 'denier' is a float (e.g. 2000.0, 18000.0) or null
        denier_val = req.get('denier')
        if denier_val is not None:
            # Handle alphanumeric deniers like \"12000 EXPO\"
            if isinstance(denier_val, (int, float)):
                d_name = str(int(denier_val))
            else:
                d_name = str(denier_val)
        else:
            # Try to infer denier from description (e.g. '12x1K' -> '12000')
            d_name = infer_denier_from_description(req.get('descripcion'))
        
        if not d_name:
            # Skip products where we can't determine the denier
            continue
        
        # Calculate h_proceso (hours on 1 post) for this reference
        rw_cap = sc_data['rewinder_capacities'].get(d_name, {})
        rw_rate = rw_cap.get('kg_per_hour', 0)
        h_proceso = kg_req / rw_rate if rw_rate > 0 else 0
        
        backlog_summary[codigo] = {
            'description': req.get('descripcion', ''),
            'kg_total': kg_req,
            'is_priority': req.get('prioridad', False),
            'denier': d_name,
            'h_proceso': h_proceso
        }
    
    # Also add manual orders (if any have cabuya_codigo set)
    for o in sc_data['orders']:
        codigo = o.get('cabuya_codigo')
        if not codigo:
            continue
        
        kg_pending = (o['total_kg'] - (o.get('produced_kg') or 0))
        if kg_pending <= 0.1:
            continue
        
        d_name = o.get('deniers', {}).get('name') if o.get('deniers') else None
        if not d_name:
            continue
        
        if codigo in backlog_summary:
            # Don't double count - automatic requirement already covers this
            pass
        else:
            rw_cap = sc_data['rewinder_capacities'].get(d_name, {})
            rw_rate = rw_cap.get('kg_per_hour', 0)
            h_proceso = kg_pending / rw_rate if rw_rate > 0 else 0
            
            backlog_summary[codigo] = {
                'description': '(Pedido Manual)',
                'kg_total': kg_pending,
                'is_priority': True,
                'denier': d_name,
                'h_proceso': h_proceso
            }

    torsion_overrides = data.get('torsion_overrides', {})
    rewinder_overrides = data.get('rewinder_overrides', {})

    result = generate_production_schedule(
        orders=sc_data['orders'],
        rewinder_capacities=sc_data['rewinder_capacities'],
        shifts=sc_data['shifts'],
        torsion_capacities=sc_data['torsion_capacities'],
        backlog_summary=backlog_summary,
        strategy=strategy,
        torsion_overrides=torsion_overrides,
        rewinder_overrides=rewinder_overrides
    )
    
    return jsonify(result)
