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
    Eres un experto en optimizacion de plantas industriales. 
    Actuas como consultor para Ciplas.
    Datos actuales:
    - Backlog: {backlog}
    - Novedades hoy: {reports}
    
    Genera un plan de accion breve y directo para maximizar la produccion.
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
      - Valid post counts = all possible sums of k operators, each with min_load..N posts.
      - We enumerate: for k=1..max_operators, range is [k*min_load, k*N]
    Returns a sorted list of valid post counts <= max_posts.
    """
    if n_optimo <= 0:
        return []
    min_load = math.ceil(0.8 * n_optimo)
    if min_load < 1:
        min_load = 1
    valid = set()
    max_operators = max_posts // min_load + 1
    for k in range(1, max_operators + 1):
        low = k * min_load
        high = k * n_optimo
        if low > max_posts:
            break
        for p in range(low, min(high, max_posts) + 1):
            valid.add(p)
    return sorted(valid)


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
    Motor de Programacion v3: Asignacion Proporcional Ajustada con Restricciones.
    Algorithm based on proportional allocation with LP heuristics:
      1. Track remaining hours (h_proceso) per reference
      2. Each shift, calculate proportional targets: target_p_r = 28 * (remaining_r / total_remaining)
      3. Generate valid post sets per reference (based on N and 80% min operator load)
      4. Greedy assignment sorted by lag ratio, with backtracking to hit exactly 28
      5. Assign operators per reference
      6. Process shift, update remaining hours, repeat
    Shifts are 8-hour blocks:
      - 24h = 3 shifts (A, B, C)
      - 16h = 2 shifts (A, B)
      - 8h  = 1 shift  (A)
      - 0h  = Closed
    """
    SHIFT_DURATION = 8
    SHIFT_DEFS = [
        {"nombre": "A", "horario": "06:00 - 14:00"},
        {"nombre": "B", "horario": "14:00 - 22:00"},
        {"nombre": "C", "horario": "22:00 - 06:00"},
    ]
    PRIORITY_FACTOR = 1.3
    MAX_OPERARIOS_TURNO = 7

    # 1. Map torsion machine speeds
    kgh_lookup = {}
    all_machines = ["T11", "T12", "T14", "T15", "T16"]
    for denier, d_data in (torsion_capacities or {}).items():
        for m in d_data.get('machines', []):
            kgh_lookup[(m['machine_id'], denier)] = m['kgh']

    # 2. Build backlog items from backlog_summary
    backlog = []
    if backlog_summary:
        for code, data in backlog_summary.items():
            if data.get('kg_total', 0) > 0.1:
                denier_name = data.get('denier')
                rw_rate = rewinder_capacities.get(denier_name, {}).get('kg_per_hour', 0)
                n_optimo = rewinder_capacities.get(denier_name, {}).get('n_optimo', 1)
                n_optimo = max(int(round(n_optimo)), 1)
                h_proceso = data.get('h_proceso', 0)
                if h_proceso <= 0 and rw_rate > 0:
                    h_proceso = float(data['kg_total']) / rw_rate
                valid_posts = _generate_valid_post_sets(n_optimo, total_rewinders)
                backlog.append({
                    "code": code, "ref": code,
                    "descripcion": data.get('description', ''),
                    "denier": denier_name,
                    "kg_pendientes": float(data['kg_total']),
                    "kg_total_inicial": float(data['kg_total']),
                    "is_priority": data.get('is_priority', False),
                    "rw_rate": rw_rate, "n_optimo": n_optimo,
                    "h_proceso_inicial": h_proceso,
                    "h_proceso_restante": h_proceso,
                    "valid_posts": valid_posts,
                })

    if not backlog:
        return {"scenario": {"resumen_global": {"comentario_estrategia": "No hay items en el backlog para programar.", "fecha_finalizacion_total": "N/A", "total_dias_programados": 0, "kg_totales_plan": 0}, "tabla_finalizacion_referencias": [], "cronograma_diario": [], "datos_para_grafica": {"labels": [], "dataset_kg_produccion": [], "dataset_operarios": []}}}

    if strategy == 'priority':
        comentario_adicional = "Asignacion proporcional balanceada -- Priorizando referencias marcadas."
    else:
        comentario_adicional = "Asignacion proporcional balanceada -- Todas las referencias avanzan simultaneamente."

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

    # 4. SIMULATION - Shift-by-shift
    while len(cronograma_final) < 60:
        active_refs = [b for b in backlog if b['h_proceso_restante'] > 0.01]
        if not active_refs:
            break
        date_str = current_date.strftime("%Y-%m-%d")
        working_hours = float(shifts_dict.get(date_str, 24))
        num_shifts = int(working_hours // SHIFT_DURATION)
        dia_entry = {"fecha": date_str, "turnos": [], "requerimiento_abastecimiento": {"kg_totales_demandados": 0, "horas_produccion_conjunta": working_hours, "detalle_torcedoras": [], "balance_por_referencia": []}}
        consumos_dia = {}
        suministros_dia = {}

        if num_shifts > 0:
            for shift_idx in range(num_shifts):
                shift_def = SHIFT_DEFS[shift_idx]
                eligibles = [b for b in backlog if b['h_proceso_restante'] > 0.01 and b['rw_rate'] > 0]
                if not eligibles:
                    break
                total_restante = sum(b['h_proceso_restante'] for b in eligibles)
                if total_restante <= 0:
                    break
                for b in eligibles:
                    raw_target = total_rewinders * (b['h_proceso_restante'] / total_restante)
                    if b['is_priority'] and strategy == 'priority':
                        raw_target *= PRIORITY_FACTOR
                    b['_target'] = raw_target
                target_sum = sum(b['_target'] for b in eligibles)
                if target_sum > 0:
                    scale = total_rewinders / target_sum
                    for b in eligibles:
                        b['_target'] *= scale

                assignment = _assign_posts_proportional(eligibles, total_rewinders, MAX_OPERARIOS_TURNO, SHIFT_DURATION)

                turno_entry = {"nombre": shift_def["nombre"], "horario": shift_def["horario"], "operarios_requeridos": 0, "asignaciones": []}
                total_ops_turno = 0
                for ref_key, a in assignment.items():
                    b_ref = a['b_ref']
                    puestos = a['puestos']
                    operarios = a['operarios']
                    total_ops_turno += operarios
                    kg_real = puestos * b_ref['rw_rate'] * SHIFT_DURATION
                    kg_remaining = b_ref['kg_pendientes']
                    if kg_real > kg_remaining * 1.10:
                        kg_real = kg_remaining
                    turno_entry["asignaciones"].append({"referencia": b_ref['ref'], "descripcion": b_ref.get('descripcion', ''), "puestos": puestos, "operarios": operarios, "kg_producidos": round(kg_real, 1)})
                    hours_consumed = puestos * SHIFT_DURATION
                    b_ref['h_proceso_restante'] -= hours_consumed
                    if b_ref['h_proceso_restante'] < 0:
                        b_ref['h_proceso_restante'] = 0
                    b_ref['kg_pendientes'] -= kg_real
                    if b_ref['kg_pendientes'] < 0:
                        b_ref['kg_pendientes'] = 0
                    consumos_dia[b_ref['ref']] = consumos_dia.get(b_ref['ref'], 0) + kg_real
                    suministro_falta = kg_real
                    for m_id in all_machines:
                        if suministro_falta <= 0.001:
                            break
                        kgh_m = kgh_lookup.get((m_id, b_ref['denier']), 0)
                        if kgh_m > 0:
                            aporte = min(suministro_falta, kgh_m * SHIFT_DURATION)
                            if aporte > 0:
                                dia_entry["requerimiento_abastecimiento"]["detalle_torcedoras"].append({"maquina": m_id, "ref": b_ref['ref'], "descripcion": b_ref.get('descripcion', ''), "turno": shift_def["nombre"], "horas": round(SHIFT_DURATION, 1), "kg_aportados": round(aporte, 1)})
                                suministro_falta -= aporte
                                suministros_dia[b_ref['ref']] = suministros_dia.get(b_ref['ref'], 0) + aporte
                    if b_ref['h_proceso_restante'] <= 0.05 and b_ref['ref'] not in tabla_finalizacion_refs:
                        tabla_finalizacion_refs[b_ref['ref']] = {"referencia": b_ref['ref'], "descripcion": b_ref.get('descripcion', ''), "fecha_finalizacion": f"{date_str} Turno {shift_def['nombre']}", "puestos_promedio": puestos, "kg_totales": b_ref['kg_total_inicial']}
                turno_entry["operarios_requeridos"] = total_ops_turno
                if turno_entry["asignaciones"]:
                    dia_entry["turnos"].append(turno_entry)

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
                dia_entry["requerimiento_abastecimiento"]["balance_por_referencia"].append({"referencia": r_name, "descripcion": desc_for_ref, "kg_suministro": round(s, 1), "kg_consumo": round(c, 1), "balance": round(bal, 1), "status": "OK" if abs(bal) < 1.0 else ("EXCESO" if bal > 0 else "FALTA")})
            dia_entry["requerimiento_abastecimiento"]["kg_totales_demandados"] = round(sum(suministros_dia.values()), 1)

        cronograma_final.append(dia_entry)
        current_date += timedelta(days=1)

    # 5. Format for frontend
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
        alerta_capacidad = f"Carga completa (28 rewinders) hasta {fecha_capacidad_completa}. A partir del {fecha_carga_baja} la carga disminuye."
    elif fecha_capacidad_completa:
        alerta_capacidad = f"Carga completa (28 rewinders) durante todo el plan hasta {fecha_capacidad_completa}."
    else:
        alerta_capacidad = "No hay suficiente backlog para ocupar los 28 rewinders desde el primer dia."

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
def _assign_posts_proportional(eligibles, total_rewinders, max_operarios, shift_duration):
    """Assign exactly total_rewinders posts across eligible references."""
    if not eligibles:
        return {}
    def lag_ratio(b):
        if b['h_proceso_inicial'] > 0:
            return b['h_proceso_restante'] / b['h_proceso_inicial']
        return 0
    eligibles_sorted = sorted(eligibles, key=lag_ratio, reverse=True)
    puestos_restantes = total_rewinders
    assignment = {}

    # PASS 1: Greedy closest-to-target
    for b in eligibles_sorted:
        if puestos_restantes <= 0:
            break
        target = b.get('_target', 0)
        valid_posts = b['valid_posts']
        if not valid_posts:
            continue
        max_useful_posts = math.ceil(b['h_proceso_restante'] / shift_duration) if shift_duration > 0 else 0
        if max_useful_posts <= 0:
            continue
        candidates = [p for p in valid_posts if p <= puestos_restantes and p <= max_useful_posts]
        if not candidates:
            smallest = valid_posts[0] if valid_posts else 0
            if smallest <= puestos_restantes and smallest > 0:
                candidates = [smallest]
            else:
                continue
        best = min(candidates, key=lambda p: abs(p - target))
        n = b['n_optimo']
        operarios = math.ceil(best / n) if n > 0 else 1
        assignment[b['ref']] = {'b_ref': b, 'puestos': best, 'operarios': operarios}
        puestos_restantes -= best

    # PASS 2: Distribute residual posts
    if puestos_restantes > 0 and assignment:
        _distribute_residual(assignment, eligibles_sorted, puestos_restantes, total_rewinders)

    # PASS 3: Validate operator count
    total_ops = sum(a['operarios'] for a in assignment.values())
    if total_ops > max_operarios:
        _reduce_operators(assignment, max_operarios)
    return assignment


def _distribute_residual(assignment, eligibles, residual, total_rewinders):
    """Distribute leftover posts by increasing assignments to next valid post count."""
    remaining = residual

    # PASS A: Use existing operator headroom
    for ref_key, a in assignment.items():
        if remaining <= 0:
            break
        b = a['b_ref']
        n = b['n_optimo']
        headroom = a['operarios'] * n - a['puestos']
        if headroom > 0:
            extra = min(remaining, headroom)
            a['puestos'] += extra
            remaining -= extra
    if remaining <= 0:
        return

    # PASS B: Upgrade to next valid post count
    for b in eligibles:
        if remaining <= 0:
            break
        ref = b['ref']
        if ref not in assignment:
            continue
        current = assignment[ref]['puestos']
        valid_posts = b['valid_posts']
        n = b['n_optimo']
        upgrades = [p for p in valid_posts if p > current and (p - current) <= remaining]
        max_useful = math.ceil(b['h_proceso_restante'] / 8) if b['h_proceso_restante'] > 0 else 0
        upgrades = [p for p in upgrades if p <= max_useful]
        if upgrades:
            new_p = min(upgrades, key=lambda p: abs(p - (current + remaining)))
            delta = new_p - current
            assignment[ref]['puestos'] = new_p
            assignment[ref]['operarios'] = math.ceil(new_p / n) if n > 0 else 1
            remaining -= delta

    # PASS C: Add unassigned refs
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
                assignment[ref] = {'b_ref': b, 'puestos': best, 'operarios': math.ceil(best / n) if n > 0 else 1}
                remaining -= best

    # PASS D: Add extra operator for headroom
    if remaining > 0:
        refs_by_remaining = sorted(assignment.keys(), key=lambda k: assignment[k]['b_ref']['h_proceso_restante'], reverse=True)
        for ref_key in refs_by_remaining:
            if remaining <= 0:
                break
            a = assignment[ref_key]
            b = a['b_ref']
            n = b['n_optimo']
            current_p = a['puestos']
            current_ops = a['operarios']
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


def _reduce_operators(assignment, max_operarios):
    """If total operators exceed maximum, reduce by lowering posts in least impactful refs."""
    total_ops = sum(a['operarios'] for a in assignment.values())
    sorted_refs = sorted(assignment.keys(), key=lambda k: assignment[k]['b_ref']['h_proceso_restante'])
    while total_ops > max_operarios and sorted_refs:
        ref = sorted_refs.pop(0)
        a = assignment[ref]
        b = a['b_ref']
        n = b['n_optimo']
        if a['operarios'] <= 1:
            continue
        new_ops = a['operarios'] - 1
        new_max_puestos = new_ops * n
        valid = b['valid_posts']
        candidates = [p for p in valid if p <= new_max_puestos]
        if candidates:
            a['puestos'] = max(candidates)
            a['operarios'] = new_ops
            total_ops = sum(a2['operarios'] for a2 in assignment.values())
