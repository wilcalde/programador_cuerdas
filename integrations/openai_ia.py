import os
import math
from datetime import datetime, timedelta
from openai import OpenAI

def generate_production_schedule(orders, rewinder_capacities, shifts, torsion_capacities, backlog_summary):
    """
    Deterministic Production Engine (Python-based)
    Calculates 24/7 production flow for 28 rewinder posts using a 'Rewinder-First' strategy.
    
    Torsion Logic update: Enforce 24h limit per machine per day with 'pre-pumping'.
    """
    
    # 1. Sort backlog for strategic flow (High Denier first?)
    # Users prefers filling 28 posts. Deniers >= 12000 MUST use 28 posts.
    backlog_list = []
    for ref, data in backlog_summary.items():
        backlog_list.append({
            "name": ref,
            "kg_total": data['kg_total']
        })
    
    # Sort descending by denier to prioritize higher volume/slower refs if needed or as strategy
    backlog_list.sort(key=lambda x: int(x['name']) if x['name'].isdigit() else 0, reverse=True)

    # 2. Setup Timeline
    start_date = datetime.now() + timedelta(days=1)
    start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    current_time = start_date
    
    cronograma_final = []
    tabla_finalizacion = []
    
    # 3. Process each reference in sequence (Continuous flow)
    for item in backlog_list:
        ref_name = item['name']
        kg_restantes = item['kg_total']
        
        # Get rewinder capacity
        r_cap = rewinder_capacities.get(ref_name, {"kg_per_hour": 50, "n_optimo": 10})
        kg_h_machine = r_cap['kg_per_hour']
        n_optimo = r_cap['n_optimo']
        
        # Rewinder-First: Always use 28 posts for Deniers >= 12000 or as general rule
        puestos_activos = 28
        kg_h_total_rew = kg_h_machine * puestos_activos
        operarios_reales = math.ceil(puestos_activos / n_optimo) if n_optimo > 0 else 1
        
        while kg_restantes > 0:
            fecha_str = current_time.strftime("%Y-%m-%d")
            
            # Find working hours for this specific day
            shift_data = next((s for s in shifts if s['date'] == fecha_str), None)
            total_horas_dia = shift_data['working_hours'] if shift_data else 24
            
            # Start of the segment
            inicio_bloque = current_time.strftime("%H:%M")
            horas_pasadas_hoy = current_time.hour + current_time.minute / 60
            horas_disponibles_hoy = total_horas_dia - horas_pasadas_hoy
            
            if horas_disponibles_hoy <= 0:
                current_time = (current_time + timedelta(days=1)).replace(hour=0, minute=0)
                continue

            # How much can we produce in the remaining time of 'today'?
            horas_necesarias_ref = kg_restantes / kg_h_total_rew
            horas_a_producir = min(horas_disponibles_hoy, horas_necesarias_ref)
            
            kg_producidos_bloque = horas_a_producir * kg_h_total_rew
            
            # Time update
            current_time += timedelta(hours=horas_a_producir)
            fin_bloque = "24:00" if current_time.hour == 0 and current_time.minute == 0 else current_time.strftime("%H:%M")
            
            # --- CÁLCULO DE SUMINISTRO (Upstream - Torcedoras) ---
            t_cap = torsion_capacities.get(ref_name, {})
            machines_data = t_cap.get('machines', [])
            
            if not machines_data:
                machines_data = [{"machine_id": "T-Gen", "kgh": 50}]
            
            vel_total_torsion = sum(m.get('kgh', 0) for m in machines_data)
            if vel_total_torsion <= 0: vel_total_torsion = 50
            
            horas_torsion_suministro = kg_producidos_bloque / vel_total_torsion
            
            detalle_suministro = []
            for m in machines_data:
                v_maq = m.get('kgh', 0)
                if v_maq <= 0 and len(machines_data) == 1: v_maq = 50
                
                detalle_suministro.append({
                    "maquina": m.get('machine_id', 'T-UKN'),
                    "horas": round(horas_torsion_suministro, 2),
                    "kg_aportados": round(horas_torsion_suministro * v_maq, 2),
                    "ref": ref_name
                })
            
            # Segmentación de datos por día
            dia_entry = next((d for d in cronograma_final if d["fecha"] == fecha_str), None)
            if not dia_entry:
                dia_entry = {
                    "fecha": fecha_str, 
                    "turnos_asignados": [],
                    "requerimiento_abastecimiento": {"kg_totales_demandados": 0, "horas_produccion_conjunta": 0, "detalle_torcedoras": []},
                    "metricas_dia": {"operarios_maximos": 0, "puestos_activos": 28}
                }
                cronograma_final.append(dia_entry)
            
            # Añadir bloque de rebobinado
            dia_entry["turnos_asignados"].append({
                "orden_secuencia": len(dia_entry["turnos_asignados"]) + 1,
                "referencia": ref_name,
                "hora_inicio": inicio_bloque,
                "hora_fin": fin_bloque,
                "puestos_utilizados": puestos_activos,
                "operarios_calculados": operarios_reales,
                "kg_producidos": round(kg_producidos_bloque, 2)
            })

            # Acumular requerimiento de abastecimiento del día (TEMP para post-procesado)
            dia_entry["requerimiento_abastecimiento"]["detalle_torcedoras"].extend(detalle_suministro)
            # Metricas del día
            dia_entry["metricas_dia"]["operarios_maximos"] = max(dia_entry["metricas_dia"]["operarios_maximos"], operarios_reales)
            
            kg_restantes -= kg_producidos_bloque
            
            if fin_bloque == "24:00":
                current_time = (current_time + timedelta(minutes=1)).replace(hour=0, minute=0, second=0, microsecond=0)

        tabla_finalizacion.append({
            "referencia": ref_name,
            "fecha_finalizacion": (current_time - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M"),
            "puestos_promedio": puestos_activos,
            "kg_totales": round(item['kg_total'], 2)
        })

    # 4. Post-Procesado de Suministro (Torcedoras 24h Cap & Pre-pumping)
    machine_daily_loads = {} 
    
    for dia in cronograma_final:
        f = dia["fecha"]
        if f not in machine_daily_loads: machine_daily_loads[f] = {}
        
        for det in dia["requerimiento_abastecimiento"]["detalle_torcedoras"]:
            m_id = det["maquina"]
            if m_id not in machine_daily_loads[f]:
                machine_daily_loads[f][m_id] = {"total_hours": 0, "kg_total": 0, "refs": set()}
            machine_daily_loads[f][m_id]["total_hours"] += det["horas"]
            machine_daily_loads[f][m_id]["kg_total"] += det["kg_aportados"]
            machine_daily_loads[f][m_id]["refs"].add(det["ref"])

    # Lógica de Pre-pumping (hacia atrás)
    sorted_dates = sorted(machine_daily_loads.keys(), reverse=True)
    for i, f in enumerate(sorted_dates):
        for m_id, load in machine_daily_loads[f].items():
            if load["total_hours"] > 24:
                over_hours = load["total_hours"] - 24
                ratio = 24 / load["total_hours"]
                kg_to_move = load["kg_total"] * (1 - ratio)
                
                load["kg_total"] *= ratio
                load["total_hours"] = 24
                
                if i + 1 < len(sorted_dates):
                    prev_f = sorted_dates[i+1]
                    if m_id not in machine_daily_loads[prev_f]:
                        machine_daily_loads[prev_f][m_id] = {"total_hours": 0, "kg_total": 0, "refs": set()}
                    
                    machine_daily_loads[prev_f][m_id]["total_hours"] += over_hours
                    machine_daily_loads[prev_f][m_id]["kg_total"] += kg_to_move
                    machine_daily_loads[prev_f][m_id]["refs"].update(load["refs"])

    for dia in cronograma_final:
        f = dia["fecha"]
        loads = machine_daily_loads.get(f, {})
        detalle_final = []
        kg_dia = 0
        horas_max = 0
        for m_id, data in loads.items():
            detalle_final.append({
                "maquina": m_id,
                "ref": ", ".join(list(data["refs"])),
                "horas": round(data["total_hours"], 2),
                "kg_aportados": round(data["kg_total"], 2)
            })
            kg_dia += data["kg_total"]
            horas_max = max(horas_max, data["total_hours"])
        
        dia["requerimiento_abastecimiento"] = {
            "kg_totales_demandados": round(kg_dia, 2),
            "horas_produccion_conjunta": round(horas_max, 2),
            "detalle_torcedoras": detalle_final
        }

    # 5. Graph Data Generation
    graph_labels = [d["fecha"] for d in cronograma_final]
    dataset_operarios = [d["metricas_dia"]["operarios_maximos"] for d in cronograma_final]
    dataset_kg = [round(d["requerimiento_abastecimiento"]["kg_totales_demandados"], 2) for d in cronograma_final]

    datos_grafica = {
        "labels": graph_labels,
        "dataset_operarios": dataset_operarios,
        "dataset_kg_produccion": dataset_kg
    }

    # 6. AI Commentary
    comentario = "Suministro T11-T16 limitado a 24h/día con pre-producción optimizada."
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        client = OpenAI(api_key=api_key)
        try:
            ai_res = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Analista de producción. Resume el plan de abastecimiento 24h y operarios en una frase corta técnica."},
                    {"role": "user", "content": f"Programados {len(backlog_list)} días. Suministro torcedoras limitado a 24h con pre-bombeo."}
                ],
                max_tokens=60
            )
            comentario = ai_res.choices[0].message.content
        except: pass

    return {
        "scenario": {
            "resumen_global": {
                "total_dias_programados": len(cronograma_final),
                "fecha_finalizacion_total": (current_time - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M"),
                "comentario_estrategia": comentario
            },
            "tabla_finalizacion_referencias": tabla_finalizacion,
            "cronograma_diario": cronograma_final,
            "datos_para_grafica": datos_grafica
        }
    }
