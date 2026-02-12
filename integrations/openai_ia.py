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

def generate_production_schedule(orders: List[Dict[str, Any]], rewinder_capacities: Dict[str, Dict], total_rewinders: int = 28, shifts: List[Dict[str, Any]] = None, torsion_capacities: Dict[str, Dict] = None, backlog_summary: Dict[str, Any] = None, strategy: str = 'kg') -> Dict[str, Any]:
    """
    Motor de Programación v2: Turnos Rotativos de 8 Horas (A, B, C).
    
    Each working day is divided into 8-hour shifts:
      - 24h → 3 shifts (A: 06:00-14:00, B: 14:00-22:00, C: 22:00-06:00)
      - 16h → 2 shifts (A, B)
      - 8h  → 1 shift  (A)
      - 0h  → Closed
    
    Within each shift, references are assigned to rewinder posts for the FULL
    8 hours. The assignment is stable: an operator keeps the same references
    for the entire shift. A slight over/under-production is acceptable to
    complete the 8h block.
    """
    
    SHIFT_DURATION = 8  # hours per shift
    SHIFT_DEFS = [
        {"nombre": "A", "horario": "06:00 - 14:00"},
        {"nombre": "B", "horario": "14:00 - 22:00"},
        {"nombre": "C", "horario": "22:00 - 06:00"},
    ]
    
    # 1. Mapeo de velocidades por máquina (torsión)
    kgh_lookup = {}
    all_machines = ["T11", "T12", "T14", "T15", "T16"]
    for denier, d_data in (torsion_capacities or {}).items():
        for m in d_data.get('machines', []):
            kgh_lookup[(m['machine_id'], denier)] = m['kgh']

    # 2. Preparar Backlog EXCLUSIVAMENTE desde backlog_summary
    backlog = []
    if backlog_summary:
        for code, data in backlog_summary.items():
            if data.get('kg_total', 0) > 0.1:
                denier_name = data.get('denier')
                rw_rate = rewinder_capacities.get(denier_name, {}).get('kg_per_hour', 0)
                n_optimo = rewinder_capacities.get(denier_name, {}).get('n_optimo', 1)
                
                backlog.append({
                    "code": code,
                    "ref": code,
                    "descripcion": data.get('description', ''),
                    "denier": denier_name,
                    "kg_pendientes": float(data['kg_total']),
                    "kg_total_inicial": float(data['kg_total']),
                    "is_priority": data.get('is_priority', False),
                    "rw_rate": rw_rate,
                    "n_optimo": max(n_optimo, 1)
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

    # Sort backlog by strategy
    if strategy == 'priority':
        backlog.sort(key=lambda x: (not x['is_priority'], -x['kg_pendientes']))
        comentario_adicional = "Priorizando referencias marcadas como PRIORIDAD ⭐."
    else:
        backlog.sort(key=lambda x: (-x['rw_rate'], -x['kg_pendientes']))
        comentario_adicional = "Maximizando flujo de producción (Kg/h) 📈."

    # 3. Calendar configuration
    default_start = datetime.now() + timedelta(days=1)
    current_date = default_start.replace(hour=0, minute=0, second=0, microsecond=0)
    if shifts and len(shifts) > 0:
        try: current_date = datetime.strptime(shifts[0]['date'], '%Y-%m-%d')
        except: pass

    shifts_dict = {s['date']: s['working_hours'] for s in shifts} if shifts else {}

    cronograma_final = []
    tabla_finalizacion_refs = {}
    total_kg_backlog = sum(b['kg_pendientes'] for b in backlog)
    total_kg_inicial = total_kg_backlog

    # 4. SIMULATION - Shift-by-shift
    while total_kg_backlog > 0.01 and len(cronograma_final) < 60:
        date_str = current_date.strftime("%Y-%m-%d")
        working_hours = float(shifts_dict.get(date_str, 24))
        
        # Determine how many shifts this day has
        num_shifts = int(working_hours // SHIFT_DURATION)
        
        dia_entry = {
            "fecha": date_str,
            "turnos": [],  # NEW: list of shift objects
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
                
                turno_entry = {
                    "nombre": shift_def["nombre"],
                    "horario": shift_def["horario"],
                    "operarios_requeridos": 0,
                    "asignaciones": []
                }
                
                puestos_disponibles = total_rewinders
                total_ops_turno = 0
                
                # Get eligible items (still have pending kg)
                eligibles = [b for b in backlog if b['kg_pendientes'] > 0.01 and b['rw_rate'] > 0]
                
                for b_ref in eligibles:
                    if puestos_disponibles <= 0:
                        break
                    
                    # Assign posts: use n_optimo as the unit block
                    n_optimo = b_ref['n_optimo']
                    # Assign as many n_optimo blocks as possible, but don't exceed available
                    puestos_asignados = min(n_optimo, puestos_disponibles)
                    
                    if puestos_asignados <= 0:
                        continue
                    
                    # Calculate production for the full 8 hours with these posts
                    capacidad_h = puestos_asignados * b_ref['rw_rate']
                    kg_en_turno = capacidad_h * SHIFT_DURATION
                    
                    # If reference finishes before 8h, still assign for the period
                    # but record actual kg (allow slight over to fill shift)
                    kg_real = min(kg_en_turno, b_ref['kg_pendientes'])
                    
                    # If the reference would finish in less than 1 hour remaining,
                    # allow producing the full shift block (slight over-production)
                    horas_necesarias = b_ref['kg_pendientes'] / capacidad_h if capacidad_h > 0 else SHIFT_DURATION
                    if horas_necesarias >= (SHIFT_DURATION * 0.5):
                        # More than half the shift needed - assign full shift
                        kg_real = kg_en_turno if kg_en_turno <= b_ref['kg_pendientes'] else b_ref['kg_pendientes']
                    else:
                        # Less than half shift - produce what's needed, free up posts
                        kg_real = b_ref['kg_pendientes']
                    
                    if kg_real < 0.01:
                        continue
                    
                    # Calculate operators for this assignment
                    operarios = math.ceil(puestos_asignados / n_optimo)
                    total_ops_turno += operarios
                    
                    turno_entry["asignaciones"].append({
                        "referencia": b_ref['ref'],
                        "descripcion": b_ref.get('descripcion', ''),
                        "puestos": puestos_asignados,
                        "operarios": operarios,
                        "kg_producidos": round(kg_real, 1)
                    })
                    
                    # Track consumption
                    consumos_dia[b_ref['ref']] = consumos_dia.get(b_ref['ref'], 0) + kg_real
                    b_ref['kg_pendientes'] -= kg_real
                    puestos_disponibles -= puestos_asignados
                    
                    # Supply from torsion machines
                    suministro_falta = kg_real
                    duracion_suministro = SHIFT_DURATION
                    for m_id in all_machines:
                        if suministro_falta <= 0.001:
                            break
                        kgh_m = kgh_lookup.get((m_id, b_ref['denier']), 0)
                        if kgh_m > 0:
                            aporte = min(suministro_falta, kgh_m * duracion_suministro)
                            if aporte > 0:
                                dia_entry["requerimiento_abastecimiento"]["detalle_torcedoras"].append({
                                    "maquina": m_id,
                                    "ref": b_ref['ref'],
                                    "descripcion": b_ref.get('descripcion', ''),
                                    "turno": shift_def["nombre"],
                                    "horas": round(duracion_suministro, 1),
                                    "kg_aportados": round(aporte, 1)
                                })
                                suministro_falta -= aporte
                                suministros_dia[b_ref['ref']] = suministros_dia.get(b_ref['ref'], 0) + aporte
                    
                    # Check if reference is completed
                    if b_ref['kg_pendientes'] <= 0.05 and b_ref['ref'] not in tabla_finalizacion_refs:
                        tabla_finalizacion_refs[b_ref['ref']] = {
                            "referencia": b_ref['ref'],
                            "descripcion": b_ref.get('descripcion', ''),
                            "fecha_finalizacion": f"{date_str} Turno {shift_def['nombre']}",
                            "puestos_promedio": puestos_asignados,
                            "kg_totales": b_ref['kg_total_inicial']
                        }
                
                turno_entry["operarios_requeridos"] = total_ops_turno
                if turno_entry["asignaciones"]:
                    dia_entry["turnos"].append(turno_entry)

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
        total_kg_backlog = sum(b['kg_pendientes'] for b in backlog)
        current_date += timedelta(days=1)

    # 5. Format for frontend
    labels = [d['fecha'] for d in cronograma_final]
    kg_data = [d['requerimiento_abastecimiento']['kg_totales_demandados'] for d in cronograma_final]
    # Max operators across all shifts in a day
    ops_data = []
    for d in cronograma_final:
        day_max_ops = 0
        for t in d.get('turnos', []):
            day_max_ops = max(day_max_ops, t['operarios_requeridos'])
        ops_data.append(day_max_ops)

    return {
        "scenario": {
            "resumen_global": {
                "comentario_estrategia": comentario_adicional,
                "fecha_finalizacion_total": cronograma_final[-1]['fecha'] if cronograma_final else "N/A",
                "total_dias_programados": len(cronograma_final),
                "kg_totales_plan": round(total_kg_inicial, 1)
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
