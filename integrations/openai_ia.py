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
    Motor de Programación: Simulación de Continuidad de Masa y Balance de Inventario.
    Uses ONLY the backlog_summary provided by app.py (from inventarios_cabuyas).
    """
    
    # 1. Mapeo de velocidades por máquina
    kgh_lookup = {}
    all_machines = ["T11", "T12", "T14", "T15", "T16"]
    for denier, d_data in torsion_capacities.items():
        for m in d_data.get('machines', []):
            kgh_lookup[(m['machine_id'], denier)] = m['kgh']

    # 2. Preparar Backlog EXCLUSIVAMENTE desde backlog_summary
    # NO HAY FALLBACK - si backlog_summary está vacío, el plan será vacío
    backlog = []
    if backlog_summary:
        for code, data in backlog_summary.items():
            if data.get('kg_total', 0) > 0.1:
                denier_name = data.get('denier')
                rw_rate = rewinder_capacities.get(denier_name, {}).get('kg_per_hour', 0)
                n_optimo = rewinder_capacities.get(denier_name, {}).get('n_optimo', 1)
                
                backlog.append({
                    "code": code,         # Product code (e.g. CAB04456)
                    "ref": code,           # Reference = product code ONLY
                    "denier": denier_name,  # Denier name for capacity lookup
                    "kg_pendientes": float(data['kg_total']),
                    "kg_total_inicial": float(data['kg_total']),
                    "is_priority": data.get('is_priority', False),
                    "rw_rate": rw_rate,
                    "n_optimo": n_optimo
                })

    # If no backlog items, return empty schedule
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

    # Ordenar según estrategia
    if strategy == 'priority':
        backlog.sort(key=lambda x: (not x['is_priority'], -x['kg_pendientes']))
        comentario_adicional = "Priorizando referencias marcadas como PRIORIDAD ⭐."
    else:
        backlog.sort(key=lambda x: (-x['rw_rate'], -x['kg_pendientes']))
        comentario_adicional = "Maximizando flujo de producción (Kg/h) 📈."

    # 3. Configuración de Calendario
    def fmt_h(val):
        h = int(val); m = int(round((val - h) * 60))
        if m >= 60: h += 1; m = 0
        return f"{h:02d}:{m:02d}"

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

    # 4. Simulación
    while total_kg_backlog > 0.01 and len(cronograma_final) < 60:
        date_str = current_date.strftime("%Y-%m-%d")
        working_hours = float(shifts_dict.get(date_str, 24))
        
        dia_entry = {
            "fecha": date_str,
            "turnos_asignados": [],
            "requerimiento_abastecimiento": {
                "kg_totales_demandados": 0,
                "horas_produccion_conjunta": working_hours,
                "detalle_torcedoras": [],
                "balance_por_referencia": []
            }
        }

        if working_hours > 0:
            horas_restantes = working_hours
            puestos_en_uso = 0
            consumos_dia = {}
            suministros_dia = {}
            
            eligibles = [b for b in backlog if b['kg_pendientes'] > 0.01]
            
            for b_ref in eligibles:
                if puestos_en_uso >= total_rewinders: break
                
                n_ref = min(b_ref['n_optimo'], total_rewinders - puestos_en_uso)
                if n_ref <= 0: continue
                
                capacidad_h = n_ref * b_ref['rw_rate']
                if capacidad_h <= 0: continue
                
                duracion = min(horas_restantes, b_ref['kg_pendientes'] / capacidad_h)
                kg_producidos = capacidad_h * duracion
                
                if kg_producidos < 0.01: continue
                
                dia_entry["turnos_asignados"].append({
                    "orden_secuencia": len(dia_entry["turnos_asignados"]) + 1,
                    "referencia": b_ref['ref'],
                    "hora_inicio": fmt_h(working_hours - horas_restantes),
                    "hora_fin": fmt_h(working_hours - horas_restantes + duracion),
                    "puestos_utilizados": n_ref,
                    "operarios_calculados": math.ceil(n_ref / 12),
                    "kg_producidos": round(kg_producidos, 1)
                })
                
                consumos_dia[b_ref['ref']] = consumos_dia.get(b_ref['ref'], 0) + kg_producidos
                b_ref['kg_pendientes'] -= kg_producidos
                puestos_en_uso += n_ref
                
                suministro_falta = kg_producidos
                for m_id in all_machines:
                    if suministro_falta <= 0.001: break
                    kgh_m = kgh_lookup.get((m_id, b_ref['denier']), 0)
                    if kgh_m > 0:
                        aporte = min(suministro_falta, kgh_m * duracion)
                        if aporte > 0:
                            dia_entry["requerimiento_abastecimiento"]["detalle_torcedoras"].append({
                                "maquina": m_id,
                                "ref": b_ref['ref'],
                                "horas": round(duracion, 1),
                                "kg_aportados": round(aporte, 1)
                            })
                            suministro_falta -= aporte
                            suministros_dia[b_ref['ref']] = suministros_dia.get(b_ref['ref'], 0) + aporte
                
                if b_ref['kg_pendientes'] <= 0.05 and b_ref['ref'] not in tabla_finalizacion_refs:
                    tabla_finalizacion_refs[b_ref['ref']] = {
                        "referencia": b_ref['ref'],
                        "fecha_finalizacion": f"{date_str} {fmt_h(working_hours - horas_restantes + duracion)}",
                        "puestos_promedio": n_ref,
                        "kg_totales": b_ref['kg_total_inicial']
                    }

            # Balances
            refs_hoy = set(consumos_dia.keys()) | set(suministros_dia.keys())
            for r_name in refs_hoy:
                c = consumos_dia.get(r_name, 0)
                s = suministros_dia.get(r_name, 0)
                bal = s - c
                dia_entry["requerimiento_abastecimiento"]["balance_por_referencia"].append({
                    "referencia": r_name,
                    "kg_suministro": round(s, 1),
                    "kg_consumo": round(c, 1),
                    "balance": round(bal, 1),
                    "status": "OK" if abs(bal) < 1.0 else ("EXCESO" if bal > 0 else "FALTA")
                })
            
            dia_entry["requerimiento_abastecimiento"]["kg_totales_demandados"] = round(sum(suministros_dia.values()), 1)

        cronograma_final.append(dia_entry)
        total_kg_backlog = sum(b['kg_pendientes'] for b in backlog)
        current_date += timedelta(days=1)

    # 5. Formatear para Frontend
    labels = [d['fecha'] for d in cronograma_final]
    kg_data = [d['requerimiento_abastecimiento']['kg_totales_demandados'] for d in cronograma_final]
    ops_data = [max([t['operarios_calculados'] for t in d['turnos_asignados']] + [0]) for d in cronograma_final]

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
