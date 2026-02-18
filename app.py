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
    
    # --- Fetch raw data ---
    orders = db.get_orders()
    pending_requirements = db.get_pending_requirements()
    inventarios_cabuyas = db.get_inventarios_cabuyas()
    machine_denier_configs = db.get_machine_denier_configs()
    
    cabuya_lookup = {c['codigo']: c for c in inventarios_cabuyas}
    
    # --- Build backlog list (same logic as backlog route) ---
    backlog_list = []
    for req in pending_requirements:
        kg_req = abs(req.get('requerimientos') or 0)
        d_val = req.get('denier')
        if d_val is not None:
            d_name = str(int(d_val)) if isinstance(d_val, (int, float)) else str(d_val)
        else:
            d_name = infer_denier_from_description(req.get('descripcion'))
        backlog_list.append({
            'codigo': req['codigo'],
            'descripcion': req.get('descripcion', ''),
            'kg': kg_req,
            'denier': d_name
        })
    for o in orders:
        if o.get('cabuya_codigo'):
            kg_pending = o['total_kg']
            codigo = o['cabuya_codigo']
            cabuya_info = cabuya_lookup.get(codigo, {})
            d_val = cabuya_info.get('denier')
            if d_val is not None:
                d_name = str(int(d_val)) if isinstance(d_val, (int, float)) else str(d_val)
            else:
                d_name = infer_denier_from_description(cabuya_info.get('descripcion'))
            backlog_list.append({
                'codigo': codigo,
                'descripcion': cabuya_info.get('descripcion', '(Pedido Manual)'),
                'kg': kg_pending,
                'denier': d_name
            })
    
    total_backlog_kg = sum(item['kg'] for item in backlog_list)
    
    # --- Group by denier ---
    denier_groups = {}
    for item in backlog_list:
        d = item['denier']
        if d not in denier_groups:
            denier_groups[d] = {'kg': 0, 'references': []}
        denier_groups[d]['kg'] += item['kg']
        denier_groups[d]['references'].append({
            'codigo': item['codigo'],
            'descripcion': item['descripcion'],
            'kg': item['kg']
        })
    
    # Sort denier groups numerically  
    def denier_sort_key(k):
        try: return float(k.split(' ')[0])
        except: return 0.0
    sorted_deniers = sorted(denier_groups.keys(), key=denier_sort_key)
    denier_chart_data = []
    for d in sorted_deniers:
        denier_chart_data.append({
            'denier': d,
            'kg': round(denier_groups[d]['kg'], 2),
            'references': denier_groups[d]['references']
        })
    
    # --- Machine-Denier Kg/h configs ---
    # Build a dict: { machine_id: { denier: kgh } }
    machine_kgh = {}
    for cfg in machine_denier_configs:
        mid = cfg['machine_id']
        denier_str = str(cfg['denier'])
        rpm = cfg.get('rpm', 0)
        torsiones = cfg.get('torsiones_metro', 0)
        husos = cfg.get('husos', 0)
        if rpm > 0 and torsiones > 0 and husos > 0:
            try:
                denier_val = float(denier_str.split(' ')[0])
                kgh = (rpm / torsiones) * 60 * (denier_val / 9000000) * husos
            except:
                kgh = 0
        else:
            kgh = 0
        if mid not in machine_kgh:
            machine_kgh[mid] = {}
        machine_kgh[mid][denier_str] = round(kgh, 2)
    
    # --- Calendar: 30 days from tomorrow, Colombian holidays 2026 ---
    today = datetime.now().date()
    start_date = today + timedelta(days=1)
    end_date = start_date + timedelta(days=29)
    
    # Colombian public holidays 2026
    colombia_holidays_2026 = [
        '2026-01-01', '2026-01-12', '2026-03-23',
        '2026-03-29', '2026-03-30', '2026-04-02', '2026-04-03',
        '2026-05-01', '2026-05-18', '2026-06-08', '2026-06-15',
        '2026-06-29', '2026-07-20', '2026-08-07', '2026-08-17',
        '2026-10-12', '2026-11-02', '2026-11-16',
        '2026-12-08', '2026-12-25'
    ]
    holidays_set = set(colombia_holidays_2026)
    
    # Check for user-defined shifts in DB
    shifts_db = db.get_shifts(str(start_date), str(end_date))
    shifts_dict = {str(s['date']): s['working_hours'] for s in shifts_db}
    
    calendar_days = []
    total_available_hours = 0
    curr = start_date
    weekday_names = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    while curr <= end_date:
        date_str = str(curr)
        wd = curr.weekday()  # 0=Mon, 6=Sun
        
        # Check if user has defined hours in DB first
        if date_str in shifts_dict:
            hours = shifts_dict[date_str]
        elif date_str in holidays_set or wd == 6:  # Sunday or holiday
            hours = 0
        elif wd == 5:  # Saturday
            hours = 16
        else:  # Mon-Fri
            hours = 24
        
        is_holiday = date_str in holidays_set
        calendar_days.append({
            'date': date_str,
            'display_date': curr.strftime('%d/%m'),
            'weekday': weekday_names[wd],
            'hours': hours,
            'is_holiday': is_holiday,
            'is_weekend': wd >= 5
        })
        total_available_hours += hours
        curr += timedelta(days=1)
    
    # --- Rewinder configs for post calculation ---
    rewinder_configs = db.get_rewinder_denier_configs()
    rewinder_kgh_map = {}
    for cfg in rewinder_configs:
        tm_min = cfg.get('tm_minutos', 0)
        if tm_min > 0:
            # Formula: (60 / minutes) * 0.8 efficiency factor
            rewinder_kgh_map[str(cfg['denier'])] = (60 / tm_min) * 0.8
        else:
            rewinder_kgh_map[str(cfg['denier'])] = 0

    # --- Machine assignments ---
    machine_assignments = {
        'T11': ['2500', '4000'],
        'T12': ['6000'],
        'T15': ['3000'],
        'T14': ['12000', '18000'],
        'T16': ['2000']
    }
    
    # --- Capacity calculation per machine ---
    machine_capacity = []
    total_assigned_posts = 0
    total_kgh_flow = 0

    for machine_id, assigned_deniers in machine_assignments.items():
        available_hours = total_available_hours
        denier_details = []
        total_required_hours = 0
        machine_output_kgh = 0
        
        # Override TARGET POSTS based on User Request
        target_posts_map = {
            'T11': 4.5,
            'T12': 4.5,
            'T15': 4,
            'T16': 6,
            'T14': 7 # (6 for d12000, 1 for d18000)
        }
        
        # Sort deniers by Kg descending
        denier_kg_list = []
        for d in assigned_deniers:
            kg_for_denier = denier_groups.get(d, {}).get('kg', 0)
            denier_kg_list.append((d, kg_for_denier))
        denier_kg_list.sort(key=lambda x: x[1], reverse=True)
        
        for d, kg_for_denier in denier_kg_list:
            # Special logic for T14 spindle distribution (20 for d12000, 4 for d18000)
            if machine_id == 'T14':
                # Fetch base config without husos to recalculate with custom husos
                cfg = next((c for c in machine_denier_configs if c['machine_id'] == 'T14' and str(c['denier']) == d), None)
                if cfg:
                    rpm = cfg.get('rpm', 0)
                    torsiones = cfg.get('torsiones_metro', 0)
                    custom_husos = 20 if d == '12000' else (4 if d == '18000' else cfg.get('husos', 0))
                    try:
                        denier_val = float(d.split(' ')[0])
                        kgh = (rpm / torsiones) * 60 * (denier_val / 9000000) * custom_husos
                    except:
                        kgh = 0
                else:
                    kgh = 0
            else:
                kgh = machine_kgh.get(machine_id, {}).get(d, 0)

            if kgh > 0 and kg_for_denier > 0:
                hours_needed = kg_for_denier / kgh
                
                # Rewinder Posts calculation
                if machine_id == 'T14':
                    posts_for_this_denier = 6 if d == '12000' else (1 if d == '18000' else 0)
                else:
                    # In others, we use the target_posts_map spread across its deniers? 
                    # Usually machines like T11/T12/T15/T16 handle one main denier or a set.
                    # We'll assign the target_posts to the primary denier or equally.
                    posts_for_this_denier = target_posts_map.get(machine_id, 0) / len(assigned_deniers)
            else:
                hours_needed = 0
                posts_for_this_denier = 0
                
            total_required_hours += hours_needed
            # WIP in Kg/h: only count if it's currently "running" (has backlog)
            if kg_for_denier > 0:
                machine_output_kgh += kgh 
            
            denier_details.append({
                'denier': d,
                'kg': round(kg_for_denier, 2),
                'kgh': round(kgh, 2),
                'hours_needed': round(hours_needed, 2),
                'posts_needed': round(posts_for_this_denier, 1),
                'rw_kgh_post': round(rewinder_kgh_map.get(d, 0), 2)
            })
        
        occupancy_pct = (total_required_hours / available_hours * 100) if available_hours > 0 else 0
        has_capacity = total_required_hours <= available_hours
        
        total_kgh_flow += machine_output_kgh
        machine_final_posts = target_posts_map.get(machine_id, 0) if total_required_hours > 0 else 0
        
        # Calculate Group Consumption (Rewinders)
        group_consumption_kgh = 0
        if total_required_hours > 0:
            for dd in denier_details:
                if dd['kg'] > 0:
                    # Consumption = posts * kgh_per_post
                    group_consumption_kgh += (dd['posts_needed'] * dd['rw_kgh_post'])
        
        group_wip_delta = machine_output_kgh - group_consumption_kgh
        if group_wip_delta > 0.1:
            group_balance_status = "A favor de Torsión" # Suministro excede consumo
        elif group_wip_delta < -0.1:
            group_balance_status = "A favor de Rewinder" # Consumo excede suministro
        else:
            group_balance_status = "Balanceado"

        machine_capacity.append({
            'machine_id': machine_id,
            'assigned_deniers': assigned_deniers,
            'available_hours': available_hours,
            'required_hours': round(total_required_hours, 2),
            'occupancy_pct': round(occupancy_pct, 1),
            'has_capacity': has_capacity,
            'remaining_hours': round(available_hours - total_required_hours, 2),
            'denier_details': denier_details,
            'total_posts': round(machine_final_posts, 1),
            'group_supply_kgh': round(machine_output_kgh, 2),
            'group_consumption_kgh': round(group_consumption_kgh, 2),
            'group_wip_delta': round(group_wip_delta, 2),
            'group_balance_status': group_balance_status
        })
        total_assigned_posts += machine_final_posts

    # --- Daily Raw Material Projection ---
    # Group backlog by Machine-Denier to simulate run
    # simulation_state: { machine_id: [ {denier, kg_remaining, kgh_supply, kgh_consumption} ] }
    simulation_queues = {}
    
    # Pre-calculate kgh consumption for each denier on each machine
    for mc in machine_capacity:
        mid = mc['machine_id']
        simulation_queues[mid] = []
        for dd in mc['denier_details']:
            if dd['kg'] > 0:
                # Fix for D4000 specific rate if needed, or use calculated.
                # User said: T11 D4000 -> 11.4 Kg/h per post.
                # Let's ensure rw_kgh_post is correct here if it's 4000
                if str(dd['denier']) == '4000':
                    rw_kgh_post = 11.4
                else:
                    rw_kgh_post = dd['rw_kgh_post']
                
                # T14 logic handled in loop above? 
                # posts_needed is already strictly set for T14 (6 or 1)
                # posts_needed for others is set by target_map / n_deniers
                
                consumption_rate = dd['posts_needed'] * rw_kgh_post
                
                simulation_queues[mid].append({
                    'denier': dd['denier'],
                    'kg_remaining': dd['kg'],
                    'consumption_rate': consumption_rate,
                    'supply_rate': dd['kgh']
                })

    daily_requirements_map = {} # { date: { denier: kg_sum } }
    daily_total_chart_data = [] # [ { date, kg } ]

    for day_info in calendar_days:
        date_str = day_info['date']
        display_date = day_info['display_date']
        hours_today = day_info['hours']
        
        daily_sum = 0
        denier_sums = {}

        if hours_today > 0:
            for mid, queue in simulation_queues.items():
                # Sequential or Simultaneous?
                # T14 is Simultaneous (split spindles). Others are likely Sequential (shared spindles).
                # Let's assume T14 is simultaneous.
                # Others: processing one by one? 
                # The backlog calculation summed hours. 
                # If T11 has D2500 and D4000, and capacity check sums them, it implies they share the resource.
                # So we simulate sequential processing for non-T14 machines.
                
                simultaneous = (mid == 'T14')
                
                hours_left_for_machine = hours_today
                
                for item in queue:
                    if item['kg_remaining'] <= 0: continue
                    
                    if simultaneous:
                        # Use full hours for this item (since it has its own spindles)
                        run_hours = hours_today
                        possible_prod = item['supply_rate'] * run_hours
                        
                        # But wait, consumption is what we need (Raw Material Req for Rewinder? Or Torsion output?)
                        # "Requerimiento de materia prima" usually means what goes INTO the process.
                        # For Rewinder, raw material is Cords from Torsion. 
                        # User asks for "Kg por dia en total de los rewinder".
                        # This implies Rewinder Output (or input). 
                        # If balanced, it's roughly the same.
                        # Let's use the Minimum of Supply vs Consumption rates to be realistic?
                        # Or just the Consumption Rate?
                        # User wants "Kg por dia en total de los rewinder". Let's use consumption_rate.
                        
                        # Limit by remaining backlog (which is in Kg of Torsion output)
                        # Actual processed = min(backlog, supply_rate * hours, consumption_rate * hours)
                        # Ideally system is balanced. But let's verify.
                        # If supply > consumption, rewinder is bottleneck.
                        # If consumption > supply, torsion is bottleneck.
                        # Production is limited by the bottleneck.
                        
                        effective_rate = min(item['supply_rate'], item['consumption_rate'])
                        if effective_rate <= 0: effective_rate = item['supply_rate'] # Fallback if consumption 0
                        
                        produced_kg = effective_rate * run_hours
                        
                        # Cap at remaining backlog
                        if produced_kg > item['kg_remaining']:
                            produced_kg = item['kg_remaining']
                            
                        item['kg_remaining'] -= produced_kg
                        
                        d_name = item['denier']
                        denier_sums[d_name] = denier_sums.get(d_name, 0) + produced_kg
                        daily_sum += produced_kg

                    else:
                        # Sequential
                        if hours_left_for_machine <= 0: break
                        
                        effective_rate = min(item['supply_rate'], item['consumption_rate'])
                        if effective_rate <= 0: effective_rate = item['supply_rate']

                        max_kg_in_time = effective_rate * hours_left_for_machine
                        
                        if max_kg_in_time >= item['kg_remaining']:
                            # Finish this backlog item
                            produced_kg = item['kg_remaining']
                            time_used = produced_kg / effective_rate if effective_rate > 0 else 0
                            hours_left_for_machine -= time_used
                            item['kg_remaining'] = 0
                        else:
                            # Partial
                            produced_kg = max_kg_in_time
                            hours_left_for_machine = 0
                            item['kg_remaining'] -= produced_kg
                        
                        d_name = item['denier']
                        denier_sums[d_name] = denier_sums.get(d_name, 0) + produced_kg
                        daily_sum += produced_kg

        daily_requirements_map[date_str] = denier_sums
        daily_total_chart_data.append({
            'date': display_date,
            'kg': round(daily_sum, 0)
        })

    # Transform map for template: list of { date: ..., deniers: { '2000': 100, ... }, total: ... }
    daily_projection_table = []
    # collection of all deniers involved
    all_involved_deniers = set()
    for d_map in daily_requirements_map.values():
        all_involved_deniers.update(d_map.keys())
    sorted_involved_deniers = sorted(list(all_involved_deniers), key=lambda x: float(x.split()[0]) if x[0].isdigit() else 0)

    for day_info in calendar_days:
        d_map = daily_requirements_map.get(day_info['date'], {})
        total_day = sum(d_map.values())
        if total_day > 0:
            row = {
                'display_date': day_info['display_date'],
                'weekday': day_info['weekday'],
                'denier_values': [round(d_map.get(d, 0), 1) for d in sorted_involved_deniers],
                'total_kg': round(total_day, 1)
            }
            daily_projection_table.append(row)

    # --- Better Redistribution Suggestions ---
    for mc in machine_capacity:
        if not mc['has_capacity']:
            mc['suggestions'] = []
            for d in mc['assigned_deniers']:
                for other_mc in machine_capacity:
                    if other_mc['machine_id'] == mc['machine_id']: continue
                    other_kgh = machine_kgh.get(other_mc['machine_id'], {}).get(d, 0)
                    if other_kgh > 0 and other_mc['has_capacity'] and other_mc['remaining_hours'] > 20:
                        mc['suggestions'].append({
                            'denier': d,
                            'target_machine': other_mc['machine_id'],
                            'available_hours': other_mc['remaining_hours']
                        })

    return render_template('dashboard.html',
                         active_page='dashboard',
                         title='Dashboard',
                         total_backlog_kg=total_backlog_kg,
                         denier_chart_data=denier_chart_data,
                         calendar_days=calendar_days,
                         total_available_hours=total_available_hours,
                         machine_capacity=machine_capacity,
                         machine_assignments=machine_assignments,
                         total_assigned_posts=round(total_assigned_posts, 1),
                         wip_total_kgh=round(total_kgh_flow, 1),
                         daily_projection_table=daily_projection_table,
                         involved_deniers=sorted_involved_deniers,
                         daily_total_chart_data=daily_total_chart_data)

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
            cabuya_info = cabuya_lookup.get(codigo, {})
            d_val = cabuya_info.get('denier')
            if d_val is not None:
                d_name = str(int(d_val)) if isinstance(d_val, (int, float)) else str(d_val)
            else:
                d_name = infer_denier_from_description(cabuya_info.get('descripcion'))
            
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
                # Handle alphanumeric deniers like "12000 EXPO"
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
                    flash(f"Pedido manual de {kg}kg para {cabuya_codigo} registrado", "success")
                else:
                    flash(f"Error: No se encontró el Denier '{denier_name}' para el producto", "error")
            else:
                flash("Error: No se pudo determinar el Denier del producto", "error")
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
            # Handle alphanumeric deniers like "12000 EXPO"
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

@app.route('/api/ai_chat', methods=['POST'])
def api_ai_chat():
    data = request.json
    user_message = data.get('message')
    from db.queries import DBQueries
    db = DBQueries()
    orders = db.get_orders()
    
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



# Health check
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
