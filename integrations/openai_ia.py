from openai import OpenAI
import os
import json
from typing import List, Dict, Any, Tuple
import math
from datetime import datetime, timedelta

# ============================================================================
# VALID POST SETS GENERATOR (sin cambios)
# ============================================================================
def _generate_valid_post_sets(n_optimo: int, max_posts: int = 28) -> List[int]:
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
# NUEVA FUNCIÓN DE ASIGNACIÓN - ESTRATEGIA "DEDICATED GROUPS"
# ============================================================================
def assign_shift_dedicated(
    active_backlog: List[Dict],
    rewinder_posts_limit: int,
    torsion_capacities: Dict[str, Dict],
    shift_duration: float
) -> Tuple[List[Dict], List[Dict], int]:
    """
    Nueva lógica: 
    - Asigna máquinas completas a referencias prioritarias (mínimo cambio en torsión).
    - Prioriza cuellos de botella.
    - Llena exactamente 28 puestos con bloques válidos.
    - Usa datos dinámicos de las bases (torsion_capacities y rewinder_capacities).
    """

    # 1. Calcular prioridad (cuellos de botella)
    candidates = []
    for item in active_backlog:
        denier = item['denier']
        pending = item['kg_pendientes']
        initial = item.get('kg_total_inicial', pending)
        max_rate = 0.0
        if denier in torsion_capacities:
            max_rate = torsion_capacities[denier].get('total_kgh', 0.0)
        weight = (pending / initial) * (pending / (max_rate + 0.1)) if max_rate > 0 else 0
        candidates.append({'item': item, 'weight': weight, 'denier': denier})

    candidates.sort(key=lambda x: x['weight'], reverse=True)

    rewinder_assignments = []
    torsion_assignments = []
    posts_remaining = rewinder_posts_limit
    assigned_machine_ids = set()

    # 2. Asignar máquinas por prioridad (máximo output por referencia)
    for cand in candidates:
        if posts_remaining <= 0:
            break

        item = cand['item']
        denier = item['denier']
        rw_rate = item['rw_rate']

        # Máquinas disponibles que pueden correr esta referencia
        available_m = []
        if denier in torsion_capacities:
            for m in torsion_capacities[denier].get('machines', []):
                if m['machine_id'] not in assigned_machine_ids:
                    available_m.append(m)

        if not available_m:
            continue

        # Asignar TODAS las máquinas disponibles para esta referencia (máximo output)
        group_assign = []
        for m in available_m:
            group_assign.append({
                'maquina': m['machine_id'],
                'denier': denier,
                'husos_asignados': m['husos'],
                'husos_totales': m['husos'],
                'kgh_maquina': m['kgh'],
                'kg_turno': m['kgh'] * shift_duration,
                'operarios': 1
            })
            assigned_machine_ids.add(m['machine_id'])

        supply_kg_turno = sum(m['kg_turno'] for m in group_assign)

        # Calcular puestos rewinders ideales
        ideal_posts = supply_kg_turno / (rw_rate * shift_duration)

        # Elegir bloque válido más cercano (preferir ligeramente superior)
        valid_posts = [p for p in item['valid_posts'] if p <= posts_remaining]
        if not valid_posts:
            # rollback
            for m in group_assign:
                assigned_machine_ids.remove(m['maquina'])
            continue

        best_p = min(valid_posts, key=lambda p: (abs(p - ideal_posts), -p if p >= ideal_posts else 999))

        consumption = best_p * rw_rate * shift_duration

        rewinder_assignments.append({
            'ref': item['ref'],
            'descripcion': item['descripcion'],
            'denier': denier,
            'puestos': best_p,
            'operarios': math.ceil(best_p / item['n_optimo']),
            'kg_producidos': min(consumption, item['kg_pendientes']),
            'rw_rate_total': best_p * rw_rate
        })

        torsion_assignments.extend(group_assign)
        posts_remaining -= best_p

    # 3. FILLER: rellenar puestos restantes con referencias pequeñas
    if posts_remaining > 0:
        filler_candidates = sorted(candidates, key=lambda x: min(x['item']['valid_posts'] or [999]))
        for cand in filler_candidates:
            if posts_remaining <= 0:
                break
            item = cand['item']
            denier = item['denier']
            rw_rate = item['rw_rate']

            available_m = []
            if denier in torsion_capacities:
                for m in torsion_capacities[denier].get('machines', []):
                    if m['machine_id'] not in assigned_machine_ids:
                        available_m.append(m)

            if not available_m:
                continue

            # Asignar todas las máquinas restantes para esta referencia filler
            group_assign = []
            for m in available_m:
                group_assign.append({
                    'maquina': m['machine_id'],
                    'denier': denier,
                    'husos_asignados': m['husos'],
                    'husos_totales': m['husos'],
                    'kgh_maquina': m['kgh'],
                    'kg_turno': m['kgh'] * shift_duration,
                    'operarios': 1
                })
                assigned_machine_ids.add(m['machine_id'])

            supply_kg_turno = sum(m['kg_turno'] for m in group_assign)
            ideal_posts = supply_kg_turno / (rw_rate * shift_duration)

            valid_posts = [p for p in item['valid_posts'] if p <= posts_remaining]
            if not valid_posts:
                continue

            best_p = min(valid_posts, key=lambda p: abs(p - ideal_posts))
            if best_p < 2:  # mínimo útil
                continue

            consumption = best_p * rw_rate * shift_duration

            # Tolerancia amplia en filler
            if 0.65 <= (supply_kg_turno / consumption) <= 1.35:
                rewinder_assignments.append({
                    'ref': item['ref'],
                    'descripcion': item['descripcion'],
                    'denier': denier,
                    'puestos': best_p,
                    'operarios': math.ceil(best_p / item['n_optimo']),
                    'kg_producidos': min(consumption, item['kg_pendientes']),
                    'rw_rate_total': best_p * rw_rate
                })
                torsion_assignments.extend(group_assign)
                posts_remaining -= best_p

    return rewinder_assignments, torsion_assignments, posts_remaining


# ============================================================================
# RESTO DEL CÓDIGO (sin cambios importantes)
# ============================================================================
# ... (mantengo exactamente igual el resto del código que ya tenías)

# Solo cambio el nombre de la función en la llamada dentro de generate_production_schedule

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
    Motor de Programación v5 - Estrategia Dedicada (máquinas fijas por referencia)
    """
    
    SHIFT_DURATION = 8
    SHIFT_DEFS = [
        {"nombre": "A", "horario": "06:00 - 14:00"},
        {"nombre": "B", "horario": "14:00 - 22:00"},
        {"nombre": "C", "horario": "22:00 - 06:00"},
    ]
    
    # Build backlog (igual)
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

    # Calendar setup (igual)
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

    while len(cronograma_final) < 60:
        active_refs = [b for b in backlog if b['kg_pendientes'] > 0.1]
        if not active_refs:
            break

        date_str = current_date.strftime("%Y-%m-%d")
        working_hours = float(shifts_dict.get(date_str, 24))
        num_shifts = int(working_hours // SHIFT_DURATION)

        dia_entry = {
            "fecha": date_str,
            "turnos": [],
            "turnos_torsion": [],
            "requerimiento_abastecimiento": {"kg_totales_demandados": 0}
        }

        kg_demandados_dia = 0

        for shift_idx in range(num_shifts):
            shift_def = SHIFT_DEFS[shift_idx]
            current_active = [b for b in backlog if b['kg_pendientes'] > 0.1]
            if not current_active:
                break

            # === LLAMADA A LA NUEVA FUNCIÓN ===
            rw_assigns, tor_assigns, p_rem = assign_shift_dedicated(
                current_active, total_rewinders, torsion_capacities, SHIFT_DURATION
            )

            # Procesar rewinders (igual que antes)
            shift_rewinder_data = []
            total_ops_rewinder = 0
            for assign in rw_assigns:
                ref = assign['ref']
                kg_prod = assign['kg_producidos']
                for b in backlog:
                    if b['ref'] == ref:
                        real_kg = min(kg_prod, b['kg_pendientes'])
                        b['kg_pendientes'] -= real_kg
                        assign['kg_producidos'] = real_kg
                        if b['kg_pendientes'] <= 0.1 and ref not in tabla_finalizacion_refs:
                            tabla_finalizacion_refs[ref] = {
                                "referencia": ref,
                                "descripcion": b['descripcion'],
                                "fecha_finalizacion": f"{date_str} Turno {shift_def['nombre']}",
                                "puestos_promedio": assign['puestos'],
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

            # Torsion data (igual)
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

        # Debug info (igual)
        debug_logs = []
        for denier, cap in torsion_capacities.items():
            supply_kg = sum(t['kg_turno'] for turn in dia_entry['turnos_torsion'] for t in turn['asignaciones'] if t['denier'] == denier)
            demand_kg = sum(a['kg_producidos'] for turn in dia_entry['turnos'] for a in turn['asignaciones'] if a['denier'] == denier)
            balance_ratio = (supply_kg / demand_kg * 100) if demand_kg > 0 else 0
            debug_logs.append({
                "denier": denier,
                "capacidad_total_kgh": cap.get('total_kgh', 0),
                "suministro_kg": round(supply_kg, 1),
                "demanda_kg": round(demand_kg, 1),
                "balance_ratio": round(balance_ratio, 1)
            })

        total_posts_used = sum(t['posts_ocupados'] for t in dia_entry['turnos'])
        avg_posts_used = round(total_posts_used / max(num_shifts, 1), 1)

        dia_entry["debug_info"] = {
            "balance_torsion": debug_logs,
            "ocupacion_rewinder_avg": f"{avg_posts_used} / 28",
            "puestos_libres_promedio": round(28 - avg_posts_used, 1)
        }

        cronograma_final.append(dia_entry)
        current_date += timedelta(days=1)

    # Final stats (igual)
    labels = [d['fecha'] for d in cronograma_final]
    kg_data = [d['requerimiento_abastecimiento']['kg_totales_demandados'] for d in cronograma_final]
    ops_data = []
    for d in cronograma_final:
        mx = max((t['operarios_requeridos'] for t in d['turnos']), default=0)
        ops_data.append(mx)

    return {
        "scenario": {
            "resumen_global": {
                "comentario_estrategia": "Nueva estrategia: Máquinas dedicadas + máximo output + balance rewinder",
                "fecha_finalizacion_total": cronograma_final[-1]['fecha'] if cronograma_final else "N/A",
                "total_dias_programados": len(cronograma_final),
                "kg_totales_plan": round(total_kg_inicial, 1),
                "alerta_capacidad": "✅ Máquinas fijas por referencia"
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
