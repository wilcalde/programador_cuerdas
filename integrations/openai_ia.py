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

def generate_production_schedule(orders: List[Dict[str, Any]], rewinder_capacities: Dict[str, Dict], total_rewinders: int = 28, shifts: List[Dict[str, Any]] = None, torsion_capacities: Dict[str, Dict] = None, backlog_summary: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Motor de Programación Refactorizado: Simulación de Continuidad de Masa.
    Regla de Oro: Suministro Torsión == Consumo Rewinder.
    """
    
    # 1. Mapeo de velocidades por máquina
    kgh_lookup = {}
    all_machines = ["T11", "T12", "T14", "T15", "T16"]
    for denier, d_data in torsion_capacities.items():
        for m in d_data.get('machines', []):
            kgh_lookup[(m['machine_id'], denier)] = m['kgh']

    # 2. Preparar Backlog (SPT - Prioridad Corto Plazo)
    backlog = []
    if backlog_summary:
        for ref, data in backlog_summary.items():
            if data.get('kg_total', 0) > 0:
                backlog.append({
                    "ref": ref,
                    "kg_pendientes": data['kg_total'],
                    "kg_total_inicial": data['kg_total']
                })
    
    # 3. Preparar Turnos
    shifts_list = sorted(shifts, key=lambda x: x['date']) if shifts else []
    current_date = datetime.now() + timedelta(days=1)
    if shifts_list:
        try:
            current_date = datetime.strptime(shifts_list[0]['date'], "%Y-%m-%d")
        except: pass

    cronograma_final = []
    tabla_finalizacion = {}
    
    def fmt_h(h):
        hrs = int(h)
        mins = int((h - hrs) * 60)
        return f"{hrs:02d}:{mins:02d}"

    # 4. Simulación Loop (Día a Día)
    total_kg_backlog = sum(b['kg_pendientes'] for b in backlog)
    
    while total_kg_backlog > 0.1:
        date_str = current_date.strftime("%Y-%m-%d")
        shift_data = next((s for s in shifts_list if s['date'] == date_str), None)
        working_hours = shift_data['working_hours'] if shift_data else 24
        
        dia_entry = {
            "fecha": date_str,
            "metricas_dia": {"puestos_activos": 0, "operarios_maximos": 0},
            "turnos_asignados": [],
            "requerimiento_abastecimiento": {
                "kg_totales_demandados": 0,
                "detalle_torcedoras": [],
                "balance_por_referencia": [],
                "check_balance": {"suministro_total_kg": 0, "consumo_total_kg": 0, "diferencia_kg": 0, "balance_perfecto": False}
            }
        }

        horas_disponibles = working_hours
        while horas_disponibles > 0.01 and any(b['kg_pendientes'] > 0.1 for b in backlog):
            # Identificar mezcla óptima para este slot
            slot_refs = []
            puestos_restantes = total_rewinders
            maquinas_restantes = list(all_machines)
            
            # Intentar llenar los 28 puestos con mezcla dinámica
            for b in sorted(backlog, key=lambda x: x['kg_pendientes'], reverse=True):
                if b['kg_pendientes'] <= 0: continue
                if puestos_restantes <= 0 or not maquinas_restantes: break
                
                ref_name = b['ref']
                tasa_unit_rw = rewinder_capacities.get(ref_name, {}).get('kg_per_hour', 0)
                if tasa_unit_rw <= 0: continue
                
                n_optimo = rewinder_capacities.get(ref_name, {}).get('n_optimo', 1)

                # Priorizar Torsión: Buscar máquinas compatibles disponibles
                maquinas_compatibles = sorted([m for m in maquinas_restantes if kgh_lookup.get((m, ref_name), 0) > 0], 
                                            key=lambda x: kgh_lookup[(x, ref_name)], reverse=True)
                if not maquinas_compatibles: continue
                
                # Capacidad de suministro SUSTENTABLE para Rewinder
                capacidad_suministro_total = sum(kgh_lookup[(m, ref_name)] for m in maquinas_compatibles)
                puestos_posibles = math.floor(capacidad_suministro_total / tasa_unit_rw)
                
                # Asignación final de puestos para este slot
                puestos_asig = min(puestos_posibles, puestos_restantes)
                
                if puestos_asig > 0:
                    demanda_kgh = puestos_asig * tasa_unit_rw
                    
                    # Selección exacta de máquinas para cubrir la demanda
                    sum_suministro = 0
                    maquinas_usadas = []
                    for m in maquinas_compatibles:
                        vel = kgh_lookup[(m, ref_name)]
                        sum_suministro += vel
                        maquinas_usadas.append(m)
                        if sum_suministro >= demanda_kgh - 0.01: break
                    
                    slot_refs.append({
                        "backlog_item": b,
                        "puestos": puestos_asig,
                        "maquinas_asig": maquinas_usadas,
                        "kgh_supply": sum_suministro,
                        "kgh_consumo": demanda_kgh,
                        "n_optimo": n_optimo
                    })
                    
                    puestos_restantes -= puestos_asig
                    for m in maquinas_usadas: maquinas_restantes.remove(m)

            if not slot_refs: break

            # Duración del slot
            duracion_slot = horas_disponibles
            for item in slot_refs:
                duracion_slot = min(duracion_slot, item['backlog_item']['kg_pendientes'] / item['kgh_consumo'])
            
            # Registrar actividad
            inicio_h = working_hours - horas_disponibles
            fin_h = inicio_h + duracion_slot
            
            ops_totales_slot = 0
            for item in slot_refs:
                b = item['backlog_item']
                kg_proc = item['kgh_consumo'] * duracion_slot
                kg_supply = item['kgh_supply'] * duracion_slot
                ref_name = b['ref']
                
                dia_entry["turnos_asignados"].append({
                    "orden_secuencia": len(dia_entry["turnos_asignados"]) + 1,
                    "referencia": ref_name,
                    "hora_inicio": fmt_h(inicio_h),
                    "hora_fin": "24:00" if (fin_h > working_hours - 0.02) else fmt_h(fin_h),
                    "puestos_utilizados": item['puestos'],
                    "operarios_calculados": math.ceil(item['puestos'] / item['n_optimo']),
                    "kg_producidos": round(kg_proc, 2)
                })
                
                for m_id in item['maquinas_asig']:
                    vel = kgh_lookup[(m_id, ref_name)]
                    kg_m = vel * duracion_slot
                    dia_entry["requerimiento_abastecimiento"]["detalle_torcedoras"].append({
                        "maquina": m_id, "ref": ref_name, "horas": round(duracion_slot, 2), "kg_aportados": round(kg_m, 2)
                    })
                    dia_entry["requerimiento_abastecimiento"]["kg_totales_demandados"] += kg_m

                found_bal = next((x for x in dia_entry["requerimiento_abastecimiento"]["balance_por_referencia"] if x["referencia"] == ref_name), None)
                if found_bal:
                    found_bal["kg_suministro"] += kg_supply
                    found_bal["kg_consumo"] += kg_proc
                else:
                    dia_entry["requerimiento_abastecimiento"]["balance_por_referencia"].append({
                        "referencia": ref_name, "kg_suministro": kg_supply, "kg_consumo": kg_proc, "status": "OK", "balance": 0
                    })

                dia_entry["requerimiento_abastecimiento"]["check_balance"]["suministro_total_kg"] += kg_supply
                dia_entry["requerimiento_abastecimiento"]["check_balance"]["consumo_total_kg"] += kg_proc
                ops_totales_slot += math.ceil(item['puestos'] / item['n_optimo'])
                
                b['kg_pendientes'] -= kg_proc
                if b['kg_pendientes'] <= 0.1:
                    tabla_finalizacion[ref_name] = current_date.replace(hour=0, minute=0) + timedelta(hours=fin_h)

            dia_entry["metricas_dia"]["operarios_maximos"] = max(dia_entry["metricas_dia"]["operarios_maximos"], ops_totales_slot)
            dia_entry["metricas_dia"]["puestos_activos"] = max(dia_entry["metricas_dia"]["puestos_activos"], sum(item['puestos'] for item in slot_refs))
            horas_disponibles -= duracion_slot
            total_kg_backlog = sum(b['kg_pendientes'] for b in backlog)

        # Totales del día
        for bal in dia_entry["requerimiento_abastecimiento"]["balance_por_referencia"]:
            bal["balance"] = round(bal["kg_suministro"] - bal["kg_consumo"], 2)
            bal["kg_suministro"] = round(bal["kg_suministro"], 1)
            bal["kg_consumo"] = round(bal["kg_consumo"], 1)
        
        chk = dia_entry["requerimiento_abastecimiento"]["check_balance"]
        chk["diferencia_kg"] = round(chk["suministro_total_kg"] - chk["consumo_total_kg"], 1)
        chk["suministro_total_kg"] = round(chk["suministro_total_kg"], 1)
        chk["consumo_total_kg"] = round(chk["consumo_total_kg"], 1)
        chk["balance_perfecto"] = abs(chk["diferencia_kg"]) < 1.0

        cronograma_final.append(dia_entry)
        current_date += timedelta(days=1)
        if len(cronograma_final) > 120: break # Safety

    # 5. Reporte Final
    tabla_finalizacion_rows = []
    kg_programados_final = 0
    for b in backlog:
        f_date = tabla_finalizacion.get(b['ref'], current_date)
        tabla_finalizacion_rows.append({
            "referencia": b['ref'],
            "fecha_finalizacion": f_date.strftime("%Y-%m-%d %H:%M"),
            "puestos_promedio": "Mezcla Dinámica JIT",
            "kg_totales": round(b['kg_total_inicial'], 2)
        })
        kg_programados_final += (b['kg_total_inicial'] - max(0, b['kg_pendientes']))

    graph_labels = [d["fecha"] for d in cronograma_final]
    dataset_ops = [d["metricas_dia"]["operarios_maximos"] for d in cronograma_final]
    dataset_kg = [d["requerimiento_abastecimiento"]["check_balance"]["suministro_total_kg"] for d in cronograma_final]

    comentario = f"Planificación 1:1 Completada. {round(kg_programados_final/1000, 1)} Toneladas integradas sin déficit de suministro."
    
    return {
        "scenario": {
            "resumen_global": {
                "total_dias_programados": len(cronograma_final),
                "fecha_finalizacion_total": max(tabla_finalizacion.values()).strftime("%Y-%m-%d %H:%M") if tabla_finalizacion else date_str,
                "kg_totales_plan": round(kg_programados_final, 2),
                "comentario_estrategia": comentario
            },
            "tabla_finalizacion_referencias": tabla_finalizacion_rows,
            "cronograma_diario": cronograma_final,
            "datos_para_grafica": {
                "labels": graph_labels,
                "dataset_operarios": dataset_ops,
                "dataset_kg_produccion": dataset_kg
            }
        }
    }
