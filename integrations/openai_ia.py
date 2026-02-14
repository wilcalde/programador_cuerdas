from openai import OpenAI
import os
import json
from typing import List, Dict, Any, Tuple
import math
from datetime import datetime, timedelta

def get_ai_optimization_scenario(backlog: List[Dict[str, Any]], reports: List[Dict[str, Any]]) -> str:
    """
    Sends plant status to GPT-4o mini to generate an optimization scenario.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return "Error: OPENAI_API_KEY no configurada."

    client = OpenAI(api_key=api_key)
    
    context = f"""
    Eres un experto en optimización de plantas industriales. 
    Actúas como consultor para Ciplas.
    Datos actuales:
    - Backlog: {backlog}
    - Novedades hoy: {reports}
    
    Genera un plan de acción breve y directo para maximizar la producción.
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Consultor Senior de Procesos Industriales."},
                {"role": "user", "content": context}
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Error al consultar la IA: {e}"


# ============================================================================
# VALID POST SETS GENERATOR
# ============================================================================
def _generate_valid_post_sets(n_optimo: int, max_posts: int = 28) -> List[int]:
    """
    Generate the list of valid post counts for a reference given its N (n_optimo).
    
    Rules:
      - Each operator handles between min_load and N posts.
      - min_load = ceil(0.8 * N)
      - Valid post counts = all possible sums of k operators, where each
        operator has between min_load and N posts.
      - We enumerate: for k=1..max_operators, range is [k*min_load, k*N]
        and ALL integers in that range are valid.
    
    Returns a sorted list of valid post counts ≤ max_posts.
    """
    if n_optimo <= 0:
        return []
    
    min_load = math.ceil(0.8 * n_optimo)
    if min_load < 1:
        min_load = 1
    
    valid = set()
    max_operators = max_posts // min_load + 1  # generous upper bound
    
    for k in range(1, max_operators + 1):
        low = k * min_load
        high = k * n_optimo
        if low > max_posts:
            break
        for p in range(low, min(high, max_posts) + 1):
            valid.add(p)
    
    return sorted(valid)


# ============================================================================
# DENIER COMPATIBILITY GROUPS FOR TORSION MACHINES
# ============================================================================
DENIER_COMPAT_GROUPS = [
    {2000, 3000},
    {6000, 9000},
    {12000, 18000},
]

def _get_denier_numeric(denier_name: str) -> int:
    """Extract numeric denier value from name like '6000' or '6000 expo'."""
    try:
        return int(denier_name.split(' ')[0])
    except (ValueError, IndexError):
        return 0

def _are_deniers_compatible(denier_a: str, denier_b: str) -> bool:
    """Check if two deniers can share a torsion machine."""
    a = _get_denier_numeric(denier_a)
    b = _get_denier_numeric(denier_b)
    if a == 0 or b == 0:
        return False
    for group in DENIER_COMPAT_GROUPS:
        if a in group and b in group:
            return True
    return False


# ============================================================================
# TORSION CAPACITY CALCULATION
# ============================================================================
def calculate_max_torsion_rate(denier: str, torsion_capacities: Dict[str, Dict]) -> float:
    """
    Calculate the maximum Kg/h production rate for a given denier
    by summing up the capacity of all compatible torsion machines.
    """
    if not denier or denier not in torsion_capacities:
        return 0.0
    
    cap_data = torsion_capacities.get(denier, {})
    return cap_data.get('total_kgh', 0.0)

# ============================================================================
# GREEDY ASSIGNMENT HEURISTIC
# ============================================================================
def assign_shift_greedy(
    active_backlog: List[Dict],
    rewinder_posts_limit: int,
    torsion_capacities: Dict[str, Dict],
    shift_duration: float
) -> Tuple[List[Dict], List[Dict], int]:
    """
    Assign rewinder posts and torsion machines for a single shift.
    Refined to target 100% utilization (28 posts) and strict mass balance.
    """
    
    # 1. Weights (Bottleneck Priority)
    candidates = []
    for item in active_backlog:
        ref = item['ref']
        denier = item['denier']
        pending_kg = item['kg_pendientes']
        initial_kg = item.get('kg_total_inicial', pending_kg)
        max_rate = calculate_max_torsion_rate(denier, torsion_capacities)
        
        # Priority favors deniers with more pending volume relative to capacity
        weight = (pending_kg / initial_kg) * (pending_kg / (max_rate + 0.1))
        candidates.append({'item': item, 'weight': weight, 'max_torsion_rate': max_rate})
        
    candidates.sort(key=lambda x: x['weight'], reverse=True)
    
    rewinder_assignments = []
    torsion_assignments = []
    posts_remaining = rewinder_posts_limit
    machine_status = {}

    def _is_balanced(supply, consumption):
        if consumption == 0: return True
        # Allow slightly more tolerance for small batches to aid utilization
        return 0.8 <= (supply / consumption) <= 1.2

    # --- Pass 1: Initial Bottleneck Assignment ---
    for cand in candidates:
        if posts_remaining <= 0: break
        
        item = cand['item']
        valid = [p for p in item['valid_posts'] if p <= posts_remaining]
        if not valid: continue
        
        # Try best fit p (descending for utilization)
        for p in sorted(valid, reverse=True):
            target = p * item['rw_rate']
            temp_status = machine_status.copy()
            assigned_m = _assign_machines_for_ref_balanced(
                item['denier'], target, torsion_capacities, temp_status, shift_duration
            )
            if assigned_m:
                supply = sum(m['kg_turno'] for m in assigned_m)
                consumption = p * item['rw_rate'] * shift_duration
                if _is_balanced(supply, consumption):
                    rewinder_assignments.append({
                        'ref': item['ref'],
                        'descripcion': item['descripcion'],
                        'denier': item['denier'],
                        'puestos': p,
                        'operarios': math.ceil(p / item['n_optimo']),
                        'kg_producidos': consumption,
                        'rw_rate_total': target
                    })
                    torsion_assignments.extend([{**m, 'ref': item['ref']} for m in assigned_m])
                    machine_status = temp_status
                    posts_remaining -= p
                    break

    # --- Pass 2: Upgrade Existing or Add New (Exhaustive Fill) ---
    if posts_remaining > 0:
        for cand in candidates:
            if posts_remaining <= 0: break
            item = cand['item']
            exist = next((a for a in rewinder_assignments if a['ref'] == item['ref']), None)
            
            if exist:
                # Try upgrading to higher valid count
                possible = [p for p in item['valid_posts'] if p > exist['puestos'] and p <= exist['puestos'] + posts_remaining]
                for p_new in sorted(possible, reverse=True):
                    target = p_new * item['rw_rate']
                    temp_status = machine_status.copy()
                    # Re-pick machines for the whole ref
                    curr_m = [t['maquina'] for t in torsion_assignments if t['ref'] == item['ref']]
                    for m_id in curr_m:
                        if m_id in temp_status: del temp_status[m_id]
                        
                    assigned_m = _assign_machines_for_ref_balanced(
                        item['denier'], target, torsion_capacities, temp_status, shift_duration
                    )
                    if assigned_m:
                        supply = sum(m['kg_turno'] for m in assigned_m)
                        consumption = p_new * item['rw_rate'] * shift_duration
                        if _is_balanced(supply, consumption):
                            posts_remaining -= (p_new - exist['puestos'])
                            exist['puestos'] = p_new
                            exist['operarios'] = math.ceil(p_new / item['n_optimo'])
                            exist['kg_producidos'] = consumption
                            exist['rw_rate_total'] = target
                            torsion_assignments = [t for t in torsion_assignments if t['ref'] != item['ref']]
                            torsion_assignments.extend([{**m, 'ref': item['ref']} for m in assigned_m])
                            machine_status = temp_status
                            break
            else:
                # Try adding new reference
                valid = [p for p in item['valid_posts'] if p <= posts_remaining]
                for p in sorted(valid, reverse=True):
                    target = p * item['rw_rate']
                    temp_status = machine_status.copy()
                    assigned_m = _assign_machines_for_ref_balanced(
                        item['denier'], target, torsion_capacities, temp_status, shift_duration
                    )
                    if assigned_m:
                        supply = sum(m['kg_turno'] for m in assigned_m)
                        consumption = p * item['rw_rate'] * shift_duration
                        if _is_balanced(supply, consumption):
                            rewinder_assignments.append({
                                'ref': item['ref'],
                                'descripcion': item['descripcion'],
                                'denier': item['denier'],
                                'puestos': p,
                                'operarios': math.ceil(p / item['n_optimo']),
                                'kg_producidos': consumption,
                                'rw_rate_total': target
                            })
                            torsion_assignments.extend([{**m, 'ref': item['ref']} for m in assigned_m])
                            machine_status = temp_status
                            posts_remaining -= p
                            break
    return rewinder_assignments, torsion_assignments, posts_remaining

def _assign_machines_for_ref_balanced(denier, target_kgh, torsion_capacities, machine_status, shift_duration):
    """
    Tries to find machines to match target_kgh exactly by using partial husos if needed.
    """
    if target_kgh <= 0: return []
    
    cap_data = torsion_capacities.get(denier, {})
    # Machines not fully occupied. 
    # To support partial assignment, machine_status should track remaining husos.
    # For now, let's keep it simple: only one ref per machine, but we can use fewer husos.
    available_machines = [m for m in cap_data.get('machines', []) if m['machine_id'] not in machine_status]
    if not available_machines:
        return []
        
    machines = sorted(available_machines, key=lambda x: x['kgh'], reverse=True)
    
    selected = []
    current_kgh = 0
    
    for m in machines:
        if current_kgh >= target_kgh: break
        
        m_id = m['machine_id']
        m_max_kgh = m['kgh']
        m_total_husos = m['husos']
        
        needed_kgh = target_kgh - current_kgh
        
        if m_max_kgh <= needed_kgh:
            # Use full machine
            assigned_husos = m_total_husos
            assigned_kgh = m_max_kgh
        else:
            # Use partial machine to match exactly
            assigned_husos = math.ceil((needed_kgh / m_max_kgh) * m_total_husos)
            # Ensure we don't exceed total
            assigned_husos = min(assigned_husos, m_total_husos)
            assigned_kgh = (assigned_husos / m_total_husos) * m_max_kgh
            
        machine_status[m_id] = True # Mark as used (we don't split machines between references yet)
        current_kgh += assigned_kgh
        selected.append({
            'maquina': m_id,
            'denier': denier,
            'husos_asignados': assigned_husos,
            'husos_totales': m_total_husos,
            'kgh_maquina': assigned_kgh,
            'kg_turno': assigned_kgh * shift_duration,
            'operarios': 1
        })
            
    # Check if we met the demand (within 5% tolerance)
    if current_kgh >= target_kgh * 0.95:
        return selected
    else:
        # Rollback
        for s in selected:
            if s['maquina'] in machine_status: del machine_status[s['maquina']]
        return []

def _assign_machines_for_ref(denier, target_kgh, torsion_capacities, machine_status, shift_duration):
    """
    Select specific machines for a reference to meet target_kgh.
    """
    assignments = []
    cap_data = torsion_capacities.get(denier, {})
    # Sort machines by Kg/h descending (use best machines first)
    machines = sorted(cap_data.get('machines', []), key=lambda m: m['kgh'], reverse=True)
    
    current_prod = 0
    
    for m in machines:
        m_id = m['machine_id']
        m_kgh = m['kgh']
        
        if m_id in machine_status:
            continue # Already used
            
        if current_prod >= target_kgh:
            break
            
        # Assign machine
        machine_status[m_id] = True
        current_prod += m_kgh
        
        assignments.append({
            'maquina': m_id,
            'denier': denier,
            'husos_asignados': m['husos'], # Full machine for now
            'husos_totales': m['husos'],
            'kgh_maquina': m_kgh,
            'kg_turno': m_kgh * shift_duration,
            'operarios': 1 # Simplified
        })
        
    return assignments


# ============================================================================
# PROPORTIONAL ALLOCATION ENGINE (UPDATED TO GREEDY)
# ============================================================================
def generate_production_schedule(
    orders: List[Dict[str, Any]],
    rewinder_capacities: Dict[str, Dict],
    total_rewinders: int = 28,
    shifts: List[Dict[str, Any]] = None,
    torsion_capacities: Dict[str, Dict] = None,
    backlog_summary: Dict[str, Any] = None,
    strategy: str = 'kg'
) -> Dict[str, Any]:
    """
    Motor de Programación v4: Heurística Greedy con Balanceo Torsión-Rewinder.
    """
    
    SHIFT_DURATION = 8  # hours per shift
    SHIFT_DEFS = [
        {"nombre": "A", "horario": "06:00 - 14:00"},
        {"nombre": "B", "horario": "14:00 - 22:00"},
        {"nombre": "C", "horario": "22:00 - 06:00"},
    ]
    
    # 1. Build Backlog Items
    backlog = []
    if backlog_summary:
        for code, data in backlog_summary.items():
            if data.get('kg_total', 0) > 0.1:
                denier_name = data.get('denier')
                rw_rate = rewinder_capacities.get(denier_name, {}).get('kg_per_hour', 0)
                n_optimo = rewinder_capacities.get(denier_name, {}).get('n_optimo', 1)
                n_optimo = max(int(round(n_optimo)), 1)
                
                valid_posts = _generate_valid_post_sets(n_optimo, total_rewinders)
                
                backlog.append({
                    "ref": code,
                    "descripcion": data.get('description', ''),
                    "denier": denier_name,
                    "kg_pendientes": float(data['kg_total']),
                    "kg_total_inicial": float(data['kg_total']),
                    "is_priority": data.get('is_priority', False),
                    "rw_rate": rw_rate,
                    "n_optimo": n_optimo,
                    "valid_posts": valid_posts,
                })

    if not backlog:
        return {"scenario": {"resumen_global": {"comentario_estrategia": "No hay items en el backlog."}, "cronograma_diario": []}}

    # 2. Setup Calendar
    default_start = datetime.now() + timedelta(days=1)
    current_date = default_start.replace(hour=0, minute=0, second=0, microsecond=0)
    if shifts and len(shifts) > 0:
        try:
             current_date = datetime.strptime(shifts[0]['date'], '%Y-%m-%d')
        except: pass
    shifts_dict = {s['date']: s['working_hours'] for s in shifts} if shifts else {}

    cronograma_final = []
    tabla_finalizacion_refs = {}
    total_kg_inicial = sum(b['kg_total_inicial'] for b in backlog)
    
    # 3. Simulate Shifts
    # Limit to 60 days to prevent infinite loops
    while len(cronograma_final) < 60:
        active_refs = [b for b in backlog if b['kg_pendientes'] > 0.1]
        if not active_refs:
            break
            
        date_str = current_date.strftime("%Y-%m-%d")
        working_hours = float(shifts_dict.get(date_str, 24))
        num_shifts = int(working_hours // SHIFT_DURATION)
        
        dia_entry = {
            "fecha": date_str,
            "turnos": [], # Rewinder details
            "turnos_torsion": [], # Torsion details
            "requerimiento_abastecimiento": { "kg_totales_demandados": 0 } # Legacy field
        }
        
        kg_demandados_dia = 0
        
        for shift_idx in range(num_shifts):
            shift_def = SHIFT_DEFS[shift_idx]
            
            # Re-filter active refs for this shift
            current_active = [b for b in backlog if b['kg_pendientes'] > 0.1]
            if not current_active:
                break
                
            # EXECUTE GREEDY HEURISTIC
            rw_assigns, tor_assigns, p_rem = assign_shift_greedy(
                current_active,
                total_rewinders,
                torsion_capacities,
                SHIFT_DURATION
            )
            
            # Process Results & Update Backlog
            shift_rewinder_data = []
            total_ops_rewinder = 0
            
            for assign in rw_assigns:
                ref = assign['ref']
                kg_prod = assign['kg_producidos']
                
                # Update Backlog
                for b in backlog:
                    if b['ref'] == ref:
                        real_kg = min(kg_prod, b['kg_pendientes']) # Don't produce more than needed
                        b['kg_pendientes'] -= real_kg
                        assign['kg_producidos'] = real_kg # Correct assignment
                        
                        # Check Completion
                        if b['kg_pendientes'] <= 0.1 and ref not in tabla_finalizacion_refs:
                             tabla_finalizacion_refs[ref] = {
                                "referencia": ref,
                                "descripcion": b['descripcion'],
                                "fecha_finalizacion": f"{date_str} Turno {shift_def['nombre']}",
                                "puestos_promedio": assign['puestos'], # Snapshot
                                "kg_totales": b['kg_total_inicial']
                            }
                        break
                
                shift_rewinder_data.append({
                    "referencia": assign['ref'],
                    "descripcion": assign['descripcion'],
                    "denier": assign.get('denier', ''),
                    "puestos": assign['puestos'],
                    "operarios": assign['operarios'],
                    "kg_producidos": round(assign['kg_producidos'], 1)
                })
                total_ops_rewinder += assign['operarios']
                kg_demandados_dia += assign['kg_producidos']

            # Torsion Data Formatter
            # _assign_machines_for_ref returns internal dicts, map to UI format
            shift_torsion_data = []
            torsion_ops_count = len(set(t['maquina'] for t in tor_assigns))
            
            for t in tor_assigns:
                shift_torsion_data.append({
                    "maquina": t['maquina'],
                    "referencia": t['ref'],
                    "denier": t['denier'],
                    "husos_asignados": t['husos_asignados'],
                    "husos_totales": t['husos_totales'],
                    "kgh_maquina": round(t['kgh_maquina'], 2),
                    "kg_turno": round(t['kg_turno'], 1),
                    "operarios": t['operarios']
                })
            
            # Add to Day Entry
            dia_entry["turnos"].append({
                "nombre": shift_def["nombre"],
                "horario": shift_def["horario"],
                "operarios_requeridos": total_ops_rewinder,
                "asignaciones": shift_rewinder_data,
                "posts_ocupados": 28 - p_rem,
                "posts_libres": p_rem
            })
            
            dia_entry["turnos_torsion"].append({
                "nombre": shift_def["nombre"],
                "horario": shift_def["horario"],
                "operarios_requeridos": torsion_ops_count,
                "asignaciones": shift_torsion_data
            })
            
        dia_entry["requerimiento_abastecimiento"]["kg_totales_demandados"] = round(kg_demandados_dia, 1)
        
        # --- CALCULATION LOGS (DEBUG INFO) ---
        debug_logs = []
        for denier, cap in torsion_capacities.items():
            # Supply: Total Torsion prod for this denier today
            supply_kg = sum(t['kg_turno'] for turn in dia_entry['turnos_torsion'] for t in turn['asignaciones'] if t['denier'] == denier)
            # Demand: Total Rewinder consumption for this denier today
            demand_kg = sum(a['kg_producidos'] for turn in dia_entry['turnos'] for a in turn['asignaciones'] if a['denier'] == denier)
            
            balance_ratio = (supply_kg / demand_kg * 100) if demand_kg > 0 else 0
            
            debug_logs.append({
                "denier": denier,
                "capacidad_total_kgh": cap['total_kgh'],
                "suministro_kg": round(supply_kg, 1),
                "demanda_kg": round(demand_kg, 1),
                "balance_ratio": round(balance_ratio, 1) # Target 100%
            })
        
        # Calculate daily post stats
        total_posts_used = sum(t['posts_ocupados'] for t in dia_entry['turnos'])
        avg_posts_used = round(total_posts_used / max(num_shifts, 1), 1)
        
        dia_entry["debug_info"] = {
            "balance_torsion": debug_logs,
            "ocupacion_rewinder_avg": f"{avg_posts_used} / 28",
            "puestos_libres_promedio": round(28 - avg_posts_used, 1)
        }
        
        cronograma_final.append(dia_entry)
        current_date += timedelta(days=1)


    # Final Stats
    labels = [d['fecha'] for d in cronograma_final]
    kg_data = [d['requerimiento_abastecimiento']['kg_totales_demandados'] for d in cronograma_final]
    ops_data = []
    for d in cronograma_final:
        mx = 0
        for t in d['turnos']:
            mx = max(mx, t['operarios_requeridos'])
        ops_data.append(mx)

    return {
        "scenario": {
            "resumen_global": {
                "comentario_estrategia": "Estrategia de Optimización: Prioridad Cuellos de Botella + Balanceo Torsión.",
                "fecha_finalizacion_total": cronograma_final[-1]['fecha'] if cronograma_final else "N/A",
                "total_dias_programados": len(cronograma_final),
                "kg_totales_plan": round(total_kg_inicial, 1),
                "fecha_capacidad_completa": "Variable", 
                "alerta_capacidad": "✅ Plan optimizado para 28 bobinadoras."
            },
            "tabla_finalizacion_referencias": list(tabla_finalizacion_refs.values()),
            "cronograma_diario": cronograma_final,
            "datos_para_grafica": {
                "labels": labels,
                "dataset_kg_produccion": kg_data,
                "dataset_operarios": ops_data
            }
        }
    }
