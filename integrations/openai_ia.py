from openai import OpenAI
import os
import json
from typing import List, Dict, Any
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
# TORSION MACHINE ASSIGNMENT PER SHIFT
# ============================================================================
def _assign_torsion_for_shift(
    shift_assignments: List[Dict],
    torsion_capacities: Dict[str, Dict],
    machine_state: Dict[str, Dict],
    shift_duration: float = 8.0
) -> List[Dict]:
    """
    Assign torsion machines to supply exactly the Kg demanded by rewinder
    assignments for this shift.

    Args:
        shift_assignments: list of {referencia, denier, kg_producidos}
        torsion_capacities: {denier_name: {total_kgh, machines: [{machine_id, kgh, husos}]}}
        machine_state: {machine_id: {refs: [denier_name], husos_used: int, husos_total: int}}
                       Tracks current shift occupation (reset each shift).
        shift_duration: hours per shift (8)

    Returns:
        list of {maquina, referencia, denier, husos_asignados, husos_totales,
                 kg_turno, kgh_maquina, operarios}
    """
    SINGLE_REF_MACHINES = {"T11", "T12"}
    result = []

    # Sort demands by Kg descending (biggest demands first for best fit)
    demands = sorted(shift_assignments, key=lambda x: x['kg_producidos'], reverse=True)

    for demand in demands:
        ref = demand['referencia']
        denier = demand['denier']
        kg_needed = demand['kg_producidos']
        if kg_needed <= 0.1:
            continue

        # Find compatible machines sorted by Kg/h descending
        cap_data = torsion_capacities.get(denier, {})
        compatible_machines = sorted(
            cap_data.get('machines', []),
            key=lambda m: m['kgh'],
            reverse=True
        )

        kg_remaining = kg_needed

        for m_info in compatible_machines:
            if kg_remaining <= 0.1:
                break

            m_id = m_info['machine_id']
            m_kgh = m_info['kgh']
            m_husos = m_info.get('husos', 0)
            if m_kgh <= 0 or m_husos <= 0:
                continue

            kgh_per_huso = m_kgh / m_husos

            # Initialize machine state if not seen
            if m_id not in machine_state:
                machine_state[m_id] = {
                    'refs': [],
                    'husos_used': 0,
                    'husos_total': m_husos,
                    'deniers': []
                }

            ms = machine_state[m_id]
            husos_available = ms['husos_total'] - ms['husos_used']

            if husos_available <= 0:
                continue  # Machine fully occupied

            # Check if machine can accept this reference
            current_refs = ms['refs']
            current_deniers = ms['deniers']

            if len(current_refs) == 0:
                # Empty machine — assign freely
                pass
            elif len(current_refs) >= 2:
                continue  # Already has 2 refs
            elif len(current_refs) == 1:
                # Has 1 ref, check if we can add a second
                if m_id in SINGLE_REF_MACHINES:
                    continue  # T11/T12: only 1 ref allowed
                existing_denier = current_deniers[0]
                if not _are_deniers_compatible(existing_denier, denier):
                    continue  # Incompatible deniers

            # Calculate husos needed for this demand
            kg_per_huso_turno = kgh_per_huso * shift_duration
            husos_needed = math.ceil(kg_remaining / kg_per_huso_turno) if kg_per_huso_turno > 0 else 0

            # Clamp to available husos
            husos_assign = min(husos_needed, husos_available)
            if husos_assign <= 0:
                continue

            kg_produced = husos_assign * kgh_per_huso * shift_duration
            kg_produced = min(kg_produced, kg_remaining)  # Don't overshoot

            # Record assignment
            result.append({
                'maquina': m_id,
                'referencia': ref,
                'denier': denier,
                'husos_asignados': husos_assign,
                'husos_totales': ms['husos_total'],
                'kg_turno': round(kg_produced, 1),
                'kgh_maquina': round(m_kgh, 2),
                'operarios': 1
            })

            # Update machine state
            ms['husos_used'] += husos_assign
            if ref not in ms['refs']:
                ms['refs'].append(ref)
            if denier not in ms['deniers']:
                ms['deniers'].append(denier)

            kg_remaining -= kg_produced

    return result


# ============================================================================
# PROPORTIONAL ALLOCATION ENGINE
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
    Motor de Programación v3: Asignación Proporcional Ajustada con Restricciones.
    
    Algorithm based on proportional allocation with LP heuristics:
      1. Track remaining hours (h_proceso) per reference
      2. Each shift, calculate proportional targets: target_p_r = 28 × (remaining_r / total_remaining)
      3. Generate valid post sets per reference (based on N and 80% min operator load)
      4. Greedy assignment sorted by lag ratio, with backtracking to hit exactly 28
      5. Assign operators per reference
      6. Process shift, update remaining hours, repeat
    
    Shifts are 8-hour blocks:
      - 24h → 3 shifts (A, B, C)
      - 16h → 2 shifts (A, B)
      - 8h  → 1 shift  (A)
      - 0h  → Closed
    """
    
    SHIFT_DURATION = 8  # hours per shift
    SHIFT_DEFS = [
        {"nombre": "A", "horario": "06:00 - 14:00"},
        {"nombre": "B", "horario": "14:00 - 22:00"},
        {"nombre": "C", "horario": "22:00 - 06:00"},
    ]
    PRIORITY_FACTOR = 1.3  # boost for priority references
    MAX_OPERARIOS_TURNO = 7
    
    # 1. Map torsion machine speeds (kept for legacy supply tracking)
    kgh_lookup = {}
    husos_lookup = {}
    all_machines = ["T11", "T12", "T14", "T15", "T16"]
    for denier, d_data in (torsion_capacities or {}).items():
        for m in d_data.get('machines', []):
            kgh_lookup[(m['machine_id'], denier)] = m['kgh']
            husos_lookup[(m['machine_id'], denier)] = m.get('husos', 0)

    # 2. Build backlog items from backlog_summary
    backlog = []
    if backlog_summary:
        for code, data in backlog_summary.items():
            if data.get('kg_total', 0) > 0.1:
                denier_name = data.get('denier')
                rw_rate = rewinder_capacities.get(denier_name, {}).get('kg_per_hour', 0)
                n_optimo = rewinder_capacities.get(denier_name, {}).get('n_optimo', 1)
                n_optimo = max(int(round(n_optimo)), 1)
                
                # h_proceso: hours to process with 1 single post
                # If passed from backlog, use it; else compute from kg and rate
                h_proceso = data.get('h_proceso', 0)
                if h_proceso <= 0 and rw_rate > 0:
                    h_proceso = float(data['kg_total']) / rw_rate
                
                # Precompute valid post sets for this denier/N
                valid_posts = _generate_valid_post_sets(n_optimo, total_rewinders)
                
                backlog.append({
                    "code": code,
                    "ref": code,
                    "descripcion": data.get('description', ''),
                    "denier": denier_name,
                    "kg_pendientes": float(data['kg_total']),
                    "kg_total_inicial": float(data['kg_total']),
                    "is_priority": data.get('is_priority', False),
                    "rw_rate": rw_rate,
                    "n_optimo": n_optimo,
                    "h_proceso_inicial": h_proceso,  # total hours with 1 post
                    "h_proceso_restante": h_proceso,  # remaining hours (decremented)
                    "valid_posts": valid_posts,
                })

    # Empty schedule
    if not backlog:
        return {
            "scenario": {
                "resumen_global": {
                    "comentario_estrategia": "No hay items en el backlog para programar.",
                    "fecha_finalizacion_total": "N/A",
                    "total_dias_programados": 0,
                    "kg_totales_plan": 0
                },
                "tabla_finalizacion_referencias": [],
                "cronograma_diario": [],
                "datos_para_grafica": {
                    "labels": [],
                    "dataset_kg_produccion": [],
                    "dataset_operarios": []
                }
            }
        }

    # Strategy comment
    if strategy == 'priority':
        comentario_adicional = "Asignación proporcional balanceada — Priorizando referencias marcadas ⭐."
    else:
        comentario_adicional = "Asignación proporcional balanceada — Todas las referencias avanzan simultáneamente 📈."

    # 3. Calendar configuration
    default_start = datetime.now() + timedelta(days=1)
    current_date = default_start.replace(hour=0, minute=0, second=0, microsecond=0)
    if shifts and len(shifts) > 0:
        try:
            current_date = datetime.strptime(shifts[0]['date'], '%Y-%m-%d')
        except:
            pass

    shifts_dict = {s['date']: s['working_hours'] for s in shifts} if shifts else {}

    cronograma_final = []
    tabla_finalizacion_refs = {}
    total_kg_inicial = sum(b['kg_total_inicial'] for b in backlog)

    # ==================================================================
    # 4. SIMULATION — Shift-by-shift
    # ==================================================================
    while len(cronograma_final) < 60:
        # Check if all references are done
        active_refs = [b for b in backlog if b['h_proceso_restante'] > 0.01]
        if not active_refs:
            break
        
        date_str = current_date.strftime("%Y-%m-%d")
        working_hours = float(shifts_dict.get(date_str, 24))
        num_shifts = int(working_hours // SHIFT_DURATION)
        
        dia_entry = {
            "fecha": date_str,
            "turnos": [],
            "turnos_torsion": [],
            "requerimiento_abastecimiento": {
                "kg_totales_demandados": 0,
                "horas_produccion_conjunta": working_hours,
                "detalle_torcedoras": [],
                "balance_por_referencia": []
            }
        }

        consumos_dia = {}
        suministros_dia = {}

        if num_shifts > 0:
            for shift_idx in range(num_shifts):
                shift_def = SHIFT_DEFS[shift_idx]
                
                # ======================================================
                # STEP 1: Update remaining hours for active refs
                # ======================================================
                eligibles = [b for b in backlog if b['h_proceso_restante'] > 0.01 and b['rw_rate'] > 0]
                if not eligibles:
                    break
                
                # ======================================================
                # STEP 2: Calculate proportional targets
                # ======================================================
                total_restante = sum(b['h_proceso_restante'] for b in eligibles)
                if total_restante <= 0:
                    break
                
                for b in eligibles:
                    raw_target = total_rewinders * (b['h_proceso_restante'] / total_restante)
                    # Apply priority boost
                    if b['is_priority'] and strategy == 'priority':
                        raw_target *= PRIORITY_FACTOR
                    b['_target'] = raw_target
                
                # Renormalize targets after priority boost to sum ≤ 28
                target_sum = sum(b['_target'] for b in eligibles)
                if target_sum > 0:
                    scale = total_rewinders / target_sum
                    for b in eligibles:
                        b['_target'] *= scale
                
                # ======================================================
                # STEP 3: Greedy assignment to hit exactly 28
                # ======================================================
                assignment = _assign_posts_proportional(
                    eligibles, total_rewinders, MAX_OPERARIOS_TURNO, SHIFT_DURATION
                )
                
                # ======================================================
                # STEP 4: Build shift entry
                # ======================================================
                turno_entry = {
                    "nombre": shift_def["nombre"],
                    "horario": shift_def["horario"],
                    "operarios_requeridos": 0,
                    "asignaciones": []
                }
                
                total_ops_turno = 0
                for ref_key, a in assignment.items():
                    b_ref = a['b_ref']
                    puestos = a['puestos']
                    operarios = a['operarios']
                    total_ops_turno += operarios
                    
                    # Kg produced this shift = puestos × rw_rate × 8h
                    kg_real = puestos * b_ref['rw_rate'] * SHIFT_DURATION
                    # Don't overshoot remaining kg
                    kg_remaining = b_ref['kg_pendientes']
                    if kg_real > kg_remaining * 1.10:
                        kg_real = kg_remaining
                    
                    turno_entry["asignaciones"].append({
                        "referencia": b_ref['ref'],
                        "descripcion": b_ref.get('descripcion', ''),
                        "puestos": puestos,
                        "operarios": operarios,
                        "kg_producidos": round(kg_real, 1)
                    })
                    
                    # Update remaining hours: each post processes 1h per shift-hour
                    # hours_consumed = puestos × SHIFT_DURATION (post-hours)
                    # but h_proceso is single-post hours, so reduction = puestos × SHIFT_DURATION / 1
                    # Actually: h_proceso = kg / (rw_rate×1post), consumed = puestos * rw_rate * 8h
                    # So hours consumed from h_proceso = kg_real / rw_rate = puestos * 8
                    hours_consumed = puestos * SHIFT_DURATION
                    b_ref['h_proceso_restante'] -= hours_consumed
                    if b_ref['h_proceso_restante'] < 0:
                        b_ref['h_proceso_restante'] = 0
                    
                    b_ref['kg_pendientes'] -= kg_real
                    if b_ref['kg_pendientes'] < 0:
                        b_ref['kg_pendientes'] = 0
                    
                    # Track consumption
                    consumos_dia[b_ref['ref']] = consumos_dia.get(b_ref['ref'], 0) + kg_real
                    
                    # Supply from torsion machines
                    suministro_falta = kg_real
                    for m_id in all_machines:
                        if suministro_falta <= 0.001:
                            break
                        kgh_m = kgh_lookup.get((m_id, b_ref['denier']), 0)
                        if kgh_m > 0:
                            aporte = min(suministro_falta, kgh_m * SHIFT_DURATION)
                            if aporte > 0:
                                dia_entry["requerimiento_abastecimiento"]["detalle_torcedoras"].append({
                                    "maquina": m_id,
                                    "ref": b_ref['ref'],
                                    "descripcion": b_ref.get('descripcion', ''),
                                    "turno": shift_def["nombre"],
                                    "horas": round(SHIFT_DURATION, 1),
                                    "kg_aportados": round(aporte, 1)
                                })
                                suministro_falta -= aporte
                                suministros_dia[b_ref['ref']] = suministros_dia.get(b_ref['ref'], 0) + aporte
                    
                    # Check if reference is completed
                    if b_ref['h_proceso_restante'] <= 0.05 and b_ref['ref'] not in tabla_finalizacion_refs:
                        tabla_finalizacion_refs[b_ref['ref']] = {
                            "referencia": b_ref['ref'],
                            "descripcion": b_ref.get('descripcion', ''),
                            "fecha_finalizacion": f"{date_str} Turno {shift_def['nombre']}",
                            "puestos_promedio": puestos,
                            "kg_totales": b_ref['kg_total_inicial']
                        }
                
                turno_entry["operarios_requeridos"] = total_ops_turno
                if turno_entry["asignaciones"]:
                    dia_entry["turnos"].append(turno_entry)
                
                # ======================================================
                # STEP 5: Assign torsion machines for this shift
                # ======================================================
                torsion_demands = []
                for a_entry in turno_entry["asignaciones"]:
                    # Find denier for this reference
                    ref_code = a_entry['referencia']
                    ref_denier = ''
                    for b_item in backlog:
                        if b_item['ref'] == ref_code:
                            ref_denier = b_item['denier']
                            break
                    torsion_demands.append({
                        'referencia': ref_code,
                        'denier': ref_denier,
                        'kg_producidos': a_entry['kg_producidos']
                    })
                
                # Reset machine state per shift
                torsion_machine_state = {}
                torsion_assignments = _assign_torsion_for_shift(
                    torsion_demands,
                    torsion_capacities or {},
                    torsion_machine_state,
                    SHIFT_DURATION
                )
                
                # Build torsion shift entry
                torsion_operarios = len(set(ta['maquina'] for ta in torsion_assignments))
                torsion_shift = {
                    "nombre": shift_def["nombre"],
                    "horario": shift_def["horario"],
                    "operarios_requeridos": torsion_operarios,
                    "asignaciones": torsion_assignments
                }
                if torsion_assignments:
                    dia_entry["turnos_torsion"].append(torsion_shift)

            # Build mass balance for the day
            refs_hoy = set(consumos_dia.keys()) | set(suministros_dia.keys())
            for r_name in refs_hoy:
                c = consumos_dia.get(r_name, 0)
                s = suministros_dia.get(r_name, 0)
                bal = s - c
                desc_for_ref = ''
                for b_item in backlog:
                    if b_item['ref'] == r_name:
                        desc_for_ref = b_item.get('descripcion', '')
                        break
                dia_entry["requerimiento_abastecimiento"]["balance_por_referencia"].append({
                    "referencia": r_name,
                    "descripcion": desc_for_ref,
                    "kg_suministro": round(s, 1),
                    "kg_consumo": round(c, 1),
                    "balance": round(bal, 1),
                    "status": "OK" if abs(bal) < 1.0 else ("EXCESO" if bal > 0 else "FALTA")
                })
            
            dia_entry["requerimiento_abastecimiento"]["kg_totales_demandados"] = round(sum(suministros_dia.values()), 1)

        cronograma_final.append(dia_entry)
        current_date += timedelta(days=1)

    # ==================================================================
    # 5. Format for frontend (identical output structure)
    # ==================================================================
    labels = [d['fecha'] for d in cronograma_final]
    kg_data = [d['requerimiento_abastecimiento']['kg_totales_demandados'] for d in cronograma_final]
    ops_data = []
    for d in cronograma_final:
        day_max_ops = 0
        for t in d.get('turnos', []):
            day_max_ops = max(day_max_ops, t['operarios_requeridos'])
        ops_data.append(day_max_ops)

    # 6. Capacity Alert
    fecha_capacidad_completa = None
    fecha_carga_baja = None
    for d in cronograma_final:
        all_shifts_full = True
        if not d.get('turnos'):
            all_shifts_full = False
        else:
            for t in d['turnos']:
                total_puestos_turno = sum(a['puestos'] for a in t.get('asignaciones', []))
                if total_puestos_turno < total_rewinders:
                    all_shifts_full = False
                    break
        if all_shifts_full:
            fecha_capacidad_completa = d['fecha']
        elif fecha_capacidad_completa and not fecha_carga_baja:
            fecha_carga_baja = d['fecha']
    
    if fecha_capacidad_completa and fecha_carga_baja:
        alerta_capacidad = f"⚠️ Carga completa (28 rewinders) hasta {fecha_capacidad_completa}. A partir del {fecha_carga_baja} la carga disminuye y no se ocupan todas las máquinas."
    elif fecha_capacidad_completa:
        alerta_capacidad = f"✅ Carga completa (28 rewinders) durante todo el plan hasta {fecha_capacidad_completa}."
    else:
        alerta_capacidad = "⚠️ No hay suficiente backlog para ocupar los 28 rewinders desde el primer día."

    return {
        "scenario": {
            "resumen_global": {
                "comentario_estrategia": comentario_adicional,
                "fecha_finalizacion_total": cronograma_final[-1]['fecha'] if cronograma_final else "N/A",
                "total_dias_programados": len(cronograma_final),
                "kg_totales_plan": round(total_kg_inicial, 1),
                "fecha_capacidad_completa": fecha_capacidad_completa or "N/A",
                "alerta_capacidad": alerta_capacidad
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


# ============================================================================
# CORE: Proportional post assignment for a single shift
# ============================================================================
def _assign_posts_proportional(
    eligibles: List[Dict],
    total_rewinders: int,
    max_operarios: int,
    shift_duration: float
) -> Dict[str, Dict]:
    """
    Assign exactly `total_rewinders` posts across eligible references using
    proportional allocation with valid-set constraints.
    
    Algorithm:
      1. Sort by lag ratio (remaining/initial) descending — most lagging first
      2. For each ref, pick valid p_r closest to target_p_r
      3. If residual remains, redistribute via backtracking
      4. Validate operator count
    
    Returns: {ref_code: {b_ref, puestos, operarios}}
    """
    if not eligibles:
        return {}
    
    # Sort by lag ratio descending (most work remaining relative to initial)
    def lag_ratio(b):
        if b['h_proceso_inicial'] > 0:
            return b['h_proceso_restante'] / b['h_proceso_inicial']
        return 0
    
    eligibles_sorted = sorted(eligibles, key=lag_ratio, reverse=True)
    
    puestos_restantes = total_rewinders
    assignment = {}  # ref_code -> {b_ref, puestos, operarios}
    
    # ---- PASS 1: Greedy closest-to-target ----
    for b in eligibles_sorted:
        if puestos_restantes <= 0:
            break
        
        target = b.get('_target', 0)
        valid_posts = b['valid_posts']
        
        if not valid_posts:
            continue
        
        # End-phase: if remaining hours < smallest valid post × shift_duration
        # assign only what's needed (ceiling of remaining hours / shift_duration)
        max_useful_posts = math.ceil(b['h_proceso_restante'] / shift_duration) if shift_duration > 0 else 0
        if max_useful_posts <= 0:
            continue
        
        # Filter valid posts to those ≤ puestos_restantes AND ≤ max_useful_posts
        candidates = [p for p in valid_posts if p <= puestos_restantes and p <= max_useful_posts]
        
        if not candidates:
            # Try the smallest valid post if it fits
            smallest = valid_posts[0] if valid_posts else 0
            if smallest <= puestos_restantes and smallest > 0:
                candidates = [smallest]
            else:
                continue
        
        # Pick candidate closest to target
        best = min(candidates, key=lambda p: abs(p - target))
        
        n = b['n_optimo']
        operarios = math.ceil(best / n) if n > 0 else 1
        
        assignment[b['ref']] = {
            'b_ref': b,
            'puestos': best,
            'operarios': operarios,
        }
        puestos_restantes -= best
    
    # ---- PASS 2: Distribute residual posts ----
    if puestos_restantes > 0 and assignment:
        _distribute_residual(assignment, eligibles_sorted, puestos_restantes, total_rewinders)
    
    # ---- PASS 3: Validate operator count ----
    total_ops = sum(a['operarios'] for a in assignment.values())
    if total_ops > max_operarios:
        _reduce_operators(assignment, max_operarios)
    
    return assignment


def _distribute_residual(
    assignment: Dict[str, Dict],
    eligibles: List[Dict],
    residual: int,
    total_rewinders: int
):
    """
    Distribute leftover posts by increasing assignments to the next valid
    post count, prioritizing the most lagging references.
    """
    remaining = residual
    
    # PASS A: Use existing operator headroom (within already assigned ops × N)
    for ref_key, a in assignment.items():
        if remaining <= 0:
            break
        b = a['b_ref']
        n = b['n_optimo']
        current = a['puestos']
        ops = a['operarios']
        headroom = ops * n - current
        if headroom > 0:
            extra = min(remaining, headroom)
            a['puestos'] += extra
            remaining -= extra
    
    if remaining <= 0:
        return
    
    # PASS B: Try to upgrade to next valid post count (may add operators)
    for b in eligibles:
        if remaining <= 0:
            break
        ref = b['ref']
        if ref not in assignment:
            continue
        
        current = assignment[ref]['puestos']
        valid_posts = b['valid_posts']
        n = b['n_optimo']
        
        # Find next valid post count above current that fits
        upgrades = [p for p in valid_posts if p > current and (p - current) <= remaining]
        
        # Also limit by useful posts
        max_useful = math.ceil(b['h_proceso_restante'] / 8) if b['h_proceso_restante'] > 0 else 0
        upgrades = [p for p in upgrades if p <= max_useful]
        
        if upgrades:
            new_p = min(upgrades, key=lambda p: abs(p - (current + remaining)))
            delta = new_p - current
            assignment[ref]['puestos'] = new_p
            assignment[ref]['operarios'] = math.ceil(new_p / n) if n > 0 else 1
            remaining -= delta
    
    # PASS C: Try to add refs that weren't assigned
    if remaining > 0:
        for b in eligibles:
            if remaining <= 0:
                break
            ref = b['ref']
            if ref in assignment:
                continue
            
            valid_posts = b['valid_posts']
            if not valid_posts:
                continue
            
            max_useful = math.ceil(b['h_proceso_restante'] / 8) if b['h_proceso_restante'] > 0 else 0
            candidates = [p for p in valid_posts if p <= remaining and p <= max_useful]
            if candidates:
                best = max(candidates)
                n = b['n_optimo']
                assignment[ref] = {
                    'b_ref': b,
                    'puestos': best,
                    'operarios': math.ceil(best / n) if n > 0 else 1,
                }
                remaining -= best
    
    # PASS D: Add an extra operator to an existing reference to create headroom
    if remaining > 0:
        # Sort by h_proceso_restante descending (add ops to biggest remaining)
        refs_by_remaining = sorted(
            assignment.keys(),
            key=lambda k: assignment[k]['b_ref']['h_proceso_restante'],
            reverse=True
        )
        for ref_key in refs_by_remaining:
            if remaining <= 0:
                break
            a = assignment[ref_key]
            b = a['b_ref']
            n = b['n_optimo']
            current_p = a['puestos']
            current_ops = a['operarios']
            
            # Check if adding one more operator helps
            new_ops = current_ops + 1
            new_headroom = new_ops * n - current_p
            if new_headroom > 0:
                extra = min(remaining, new_headroom)
                max_useful = math.ceil(b['h_proceso_restante'] / 8) if b['h_proceso_restante'] > 0 else 0
                extra = min(extra, max(max_useful - current_p, 0))
                if extra > 0:
                    a['puestos'] += extra
                    a['operarios'] = new_ops
                    remaining -= extra


def _reduce_operators(assignment: Dict[str, Dict], max_operarios: int):
    """
    If total operators exceed maximum, reduce by lowering posts in references
    with the smallest remaining hours (least impact).
    """
    total_ops = sum(a['operarios'] for a in assignment.values())
    
    # Sort assignments by remaining hours ascending (reduce least impactful first)
    sorted_refs = sorted(
        assignment.keys(),
        key=lambda k: assignment[k]['b_ref']['h_proceso_restante']
    )
    
    while total_ops > max_operarios and sorted_refs:
        ref = sorted_refs.pop(0)
        a = assignment[ref]
        b = a['b_ref']
        n = b['n_optimo']
        
        if a['operarios'] <= 1:
            continue
        
        # Reduce by one operator
        new_ops = a['operarios'] - 1
        new_max_puestos = new_ops * n
        
        # Find best valid post count for new operator count
        valid = b['valid_posts']
        candidates = [p for p in valid if p <= new_max_puestos]
        if candidates:
            a['puestos'] = max(candidates)
            a['operarios'] = new_ops
            total_ops = sum(a2['operarios'] for a2 in assignment.values())
