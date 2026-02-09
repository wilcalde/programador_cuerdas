from openai import OpenAI
import os
import json
from typing import List, Dict, Any
from datetime import datetime, timedelta
import math

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
    Generate a deterministic operational production schedule in Python.
    Features HR Load Balancing (Parallel Streams) and GUARANTEED Mass Balance.
    """
    
    # 1. Prepare Backlog List (Deterministic SPT order)
    backlog_list = []
    if backlog_summary:
        for d_name, data in backlog_summary.items():
            backlog_list.append({
                "ref": d_name,
                "kg_total": data.get('kg_total', 0)
            })
    else:
        # SPT Fallback
        temp_backlog = {}
        for o in orders:
            d_name = o.get('deniers', {}).get('name', 'Unknown')
            temp_backlog[d_name] = temp_backlog.get(d_name, 0) + (o.get('total_kg', 0) - (o.get('produced_kg', 0) or 0))
        for d_name, kg in temp_backlog.items():
            if kg > 0:
                backlog_list.append({"ref": d_name, "kg_total": kg})
    
    backlog_list.sort(key=lambda x: str(x['ref']))

    # 3. Calendar Setup
    default_start_date = datetime.now() + timedelta(days=1)
    current_time = default_start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    
    if shifts and len(shifts) > 0:
        try:
            first_date = datetime.strptime(shifts[0]['date'], '%Y-%m-%d')
            current_time = first_date.replace(hour=0, minute=0, second=0, microsecond=0)
        except:
            pass

    # 4. Engine de Balanceo HR (Smooth Production)
    cronograma_final = []
    tabla_finalizacion = {} # {ref: {'fecha': str, 'kg': float}}
    
    # Preparar estado del backlog
    backlog_status = []
    for item in backlog_list:
        ref_name = str(item['ref'])
        cap = rewinder_capacities.get(ref_name, {})
        backlog_status.append({
            "ref": ref_name,
            "kg_pendientes": item['kg_total'],
            "kg_total_inicial": item['kg_total'],
            "kgh_unitario": cap.get('kg_per_hour', 0),
            "n_optimo": cap.get('n_optimo', 1),
            "ops_for_28": 28 / cap.get('n_optimo', 1) if cap.get('n_optimo', 1) > 0 else 28
        })

    def get_next_ref(exclude=None):
        for b in backlog_status:
            if b['kg_pendientes'] > 0.01 and b['ref'] != exclude:
                return b
        return None

    # Procesamiento dia a dia
    while any(b['kg_pendientes'] > 0.01 for b in backlog_status):
        fecha_str = current_time.strftime("%Y-%m-%d")
        
        # Identificar las dos streams del día
        ref_a = get_next_ref()
        if not ref_a: break
        
        # ¿Es una referencia pesada que requiere dividir puestos? (Usa más de 11 operarios para 28 puestos)
        es_pesada = ref_a['ops_for_28'] > 11
        ref_b = get_next_ref(exclude=ref_a['ref']) if es_pesada else None
        
        puestos_a = 14 if ref_b else 28
        puestos_b = 14 if ref_b else 0
        
        dia_entry = {
            "fecha": fecha_str,
            "turnos_asignados": [],
            "requerimiento_abastecimiento": {"kg_totales_demandados": 0, "horas_produccion_conjunta": 0, "detalle_torcedoras": []},
            "metricas_dia": {"operarios_maximos": 0, "puestos_activos": 28}
        }
        
        # Simular 24 horas del día
        horas_disponibles = 24.0
        while horas_disponibles > 0.01:
            # Seleccionar referencias vigentes para este slot
            curr_a = get_next_ref()
            if not curr_a: break
            
            # Si el 'ref_a' original ya terminó, recalculamos si necesitamos stream b
            es_pesada_ahora = curr_a['ops_for_28'] > 11
            curr_b = get_next_ref(exclude=curr_a['ref']) if es_pesada_ahora else None
            
            p_a = 14 if curr_b else 28
            p_b = 14 if curr_b else 0
            
            # Calcular cuánto tiempo dura este slot (hasta que alguna ref se acabe o acabe el día)
            vel_a = curr_a['kgh_unitario'] * p_a
            time_to_finish_a = curr_a['kg_pendientes'] / vel_a if vel_a > 0 else 999
            
            time_to_finish_b = 999
            if curr_b:
                vel_b = curr_b['kgh_unitario'] * p_b
                time_to_finish_b = curr_b['kg_pendientes'] / vel_b if vel_b > 0 else 999
            
            duracion_slot = min(horas_disponibles, time_to_finish_a, time_to_finish_b)
            
            # Registrar bloques
            inicio_s = (24.0 - horas_disponibles)
            fin_s = inicio_s + duracion_slot
            
            def fmt_h(val):
                h = int(val)
                m = int((val - h) * 60)
                return f"{h:02d}:{m:02d}"

            # Stream A
            kg_a = vel_a * duracion_slot
            ops_a = math.ceil(p_a / curr_a['n_optimo'])
            dia_entry["turnos_asignados"].append({
                "orden_secuencia": len(dia_entry["turnos_asignados"]) + 1,
                "referencia": curr_a['ref'],
                "hora_inicio": fmt_h(inicio_s),
                "hora_fin": "24:00" if fin_s > 23.98 else fmt_h(fin_s),
                "puestos_utilizados": p_a,
                "operarios_calculados": ops_a,
                "kg_producidos": round(kg_a, 2)
            })
            curr_a['kg_pendientes'] -= kg_a
            if curr_a['kg_pendientes'] <= 0.01:
                tabla_finalizacion[curr_a['ref']] = current_time.replace(hour=0, minute=0) + timedelta(hours=fin_s)
            
            # Stream B
            ops_b = 0
            if curr_b:
                vel_b = curr_b['kgh_unitario'] * p_b
                kg_b = vel_b * duracion_slot
                ops_b = math.ceil(p_b / curr_b['n_optimo'])
                dia_entry["turnos_asignados"].append({
                    "orden_secuencia": len(dia_entry["turnos_asignados"]) + 1,
                    "referencia": curr_b['ref'],
                    "hora_inicio": fmt_h(inicio_s),
                    "hora_fin": "24:00" if fin_s > 23.98 else fmt_h(fin_s),
                    "puestos_utilizados": p_b,
                    "operarios_calculados": ops_b,
                    "kg_producidos": round(kg_b, 2)
                })
                curr_b['kg_pendientes'] -= kg_b
                if curr_b['kg_pendientes'] <= 0.01:
                    tabla_finalizacion[curr_b['ref']] = current_time.replace(hour=0, minute=0) + timedelta(hours=fin_s)

            # Suministro (Torcedoras) - Cálculo temporal para el mass balance posterior
            for c_ref, c_kg in [(curr_a, kg_a), (curr_b, kg_b) if curr_b else (None, 0)]:
                if not c_ref: continue
                dia_entry["requerimiento_abastecimiento"]["detalle_torcedoras"].append({
                    "ref": c_ref['ref'], "kg_aportados": c_kg
                })

            dia_entry["metricas_dia"]["operarios_maximos"] = max(dia_entry["metricas_dia"]["operarios_maximos"], ops_a + ops_b)
            horas_disponibles -= duracion_slot

        cronograma_final.append(dia_entry)
        current_time += timedelta(days=1)

    # Preparar tabla finalizacion formateada
    tabla_finalizacion_rows = []
    for b in backlog_status:
        f_date = tabla_finalizacion.get(b['ref'], current_time)
        tabla_finalizacion_rows.append({
            "referencia": b['ref'],
            "fecha_finalizacion": f_date.strftime("%Y-%m-%d %H:%M"),
            "puestos_promedio": "Variable",
            "kg_totales": round(b['kg_total_inicial'], 2)
        })
    
    # --- NUEVA LÓGICA DE SUMINISTRO (Balance de Masa Garantizado) ---
    # 1. Agrupamos demanda total y fecha límite (último día que el Rewinder pide esa ref)
    demanda_total_kg = {}
    ultimo_dia_necesidad = {}
    
    for dia in cronograma_final:
        fecha = dia["fecha"]
        for det in dia["requerimiento_abastecimiento"]["detalle_torcedoras"]:
            ref = det["ref"]
            demanda_total_kg[ref] = demanda_total_kg.get(ref, 0) + det["kg_aportados"]
            ultimo_dia_necesidad[ref] = fecha 

    # 2. Capacidades y Máquinas Universales
    kgh_lookup = {}
    all_machines_in_plant = ["T11", "T12", "T14", "T15", "T16"]
    for denier, data in torsion_capacities.items():
        for m in data.get('machines', []):
            m_id = m['machine_id']
            kgh_lookup[(m_id, denier)] = m['kgh']
            if m_id not in all_machines_in_plant: all_machines_in_plant.append(m_id)

    # 3. Distribución por Objetivo de Masa (Backwards Filling)
    machine_occupancy = {} # {fecha: {machine_id: ref}}
    machine_kg_day = {} # {fecha: {machine_id: {kg, horas}}}
    
    # Ordenamos referencias: las que terminan último primero para llenar desde el final hacia atrás
    lista_refs = sorted(demanda_total_kg.keys(), key=lambda r: ultimo_dia_necesidad[r], reverse=True)
    all_scheduled_dates = sorted([d["fecha"] for d in cronograma_final])
    
    for ref in lista_refs:
        kg_faltante = demanda_total_kg[ref]
        idx_limite = all_scheduled_dates.index(ultimo_dia_necesidad[ref])
        
        # Retrocedemos desde el día límite cubriendo la masa total
        curr_idx = idx_limite
        while kg_faltante > 0.1:
            if curr_idx < 0:
                # Caso Crítico: No hay más días en el cronograma original. Creamos días previos.
                d_obj = datetime.strptime(all_scheduled_dates[0], "%Y-%m-%d") - timedelta(days=abs(curr_idx))
                f_str = d_obj.strftime("%Y-%m-%d")
            else:
                f_str = all_scheduled_dates[curr_idx]
            
            if f_str not in machine_occupancy: machine_occupancy[f_str] = {}
            if f_str not in machine_kg_day: machine_kg_day[f_str] = {}

            # Máquinas compatibles (con fallback universal)
            compatibles = sorted([m for m in all_machines_in_plant if (m, ref) in kgh_lookup])
            if not compatibles: compatibles = sorted(all_machines_in_plant)
            
            for m_id in compatibles:
                if kg_faltante <= 0.1: break
                # Especialización: 1 máquina/ref/día
                if m_id not in machine_occupancy[f_str]:
                    vel = kgh_lookup.get((m_id, ref), 50.0)
                    if vel <= 0: vel = 50.0
                    
                    horas_asig = min(24.0, kg_faltante / vel)
                    kg_asig = horas_asig * vel
                    
                    machine_occupancy[f_str][m_id] = ref
                    machine_kg_day[f_str][m_id] = {"kg": round(kg_asig, 2), "horas": round(horas_asig, 2)}
                    kg_faltante -= kg_asig
            
            curr_idx -= 1
            # Failsafe: Si kg_faltante no baja tras 100 iteraciones (ej. capacidad 0), salir
            if curr_idx < -100: break

    # 4. Re-ensamblaje y Limpieza de Datos Previos
    dates_with_torsion = sorted(machine_kg_day.keys())
    
    # Limpiamos el nodo de abastecimiento en los días originales
    for dia in cronograma_final:
        dia["requerimiento_abastecimiento"] = {"kg_totales_demandados": 0, "horas_produccion_conjunta": 0, "detalle_torcedoras": []}

    # Insertamos o actualizamos días con la nueva carga
    for f_str in dates_with_torsion:
        dia_match = next((d for d in cronograma_final if d["fecha"] == f_str), None)
        if not dia_match:
            # Crear día buffer al inicio si estamos bombeando antes de la fecha de inicio
            dia_match = {
                "fecha": f_str,
                "turnos_asignados": [],
                "requerimiento_abastecimiento": {"kg_totales_demandados": 0, "horas_produccion_conjunta": 0, "detalle_torcedoras": []},
                "metricas_dia": {"operarios_maximos": 0, "puestos_activos": 0}
            }
            cronograma_final.insert(0, dia_match)
        
        detalle = []
        kg_dia = 0
        h_max = 0
        for m_id in sorted(machine_kg_day[f_str].keys()):
            data = machine_kg_day[f_str][m_id]
            detalle.append({
                "maquina": m_id,
                "ref": machine_occupancy[f_str][m_id],
                "horas": data["horas"],
                "kg_aportados": data["kg"]
            })
            kg_dia += data["kg"]
            h_max = max(h_max, data["horas"])
        
        dia_match["requerimiento_abastecimiento"] = {
            "kg_totales_demandados": round(kg_dia, 2),
            "horas_produccion_conjunta": round(h_max, 2),
            "detalle_torcedoras": detalle
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
    comentario = "Estrategia de Balanceo HR: Producción simultánea activada para suavizar picos de operarios."
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        try:
            ai_res = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Analista de producción. Resume la estrategia de balanceo HR (puestos divididos y concurrencia) en una frase corta técnica."},
                    {"role": "user", "content": f"Programados {len(backlog_status)} referencias. Operarios max: {max(dataset_operarios)}. Segregación de denier altos completada."}
                ],
                max_tokens=60
            )
            comentario = ai_res.choices[0].message.content
        except: pass

    return {
        "scenario": {
            "resumen_global": {
                "total_dias_programados": len(cronograma_final),
                "fecha_finalizacion_total": (current_time - timedelta(days=1)).strftime("%Y-%m-%d %H:%M"),
                "comentario_estrategia": comentario
            },
            "tabla_finalizacion_referencias": tabla_finalizacion_rows,
            "cronograma_diario": cronograma_final,
            "datos_para_grafica": datos_grafica
        }
    }
