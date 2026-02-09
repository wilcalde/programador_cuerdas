from openai import OpenAI
import os
import json
from typing import List, Dict, Any

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

from datetime import datetime, timedelta
import math

def generate_production_schedule(orders: List[Dict[str, Any]], rewinder_capacities: Dict[str, Dict], total_rewinders: int = 28, shifts: List[Dict[str, Any]] = None, torsion_capacities: Dict[str, Dict] = None, backlog_summary: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Generate a deterministic operational production schedule in Python.
    No more AI-based math. GPT-4o-mini only used for scenario commentary.
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
        # SPT Fallback: Shortest Processing Time roughly approximated by denier/kg
        temp_backlog = {}
        for o in orders:
            d_name = o.get('deniers', {}).get('name', 'Unknown')
            temp_backlog[d_name] = temp_backlog.get(d_name, 0) + (o.get('total_kg', 0) - (o.get('produced_kg', 0) or 0))
        for d_name, kg in temp_backlog.items():
            if kg > 0:
                backlog_list.append({"ref": d_name, "kg_total": kg})
    
    # Sort by Ref name as simple priority for now, or maintain provided order
    backlog_list.sort(key=lambda x: str(x['ref']))

    # 2. Master Data Lookup
    
    # 3. Calendar Setup
    default_start_date = datetime.now() + timedelta(days=1)
    current_time = default_start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    
    if shifts and len(shifts) > 0:
        try:
            first_date = datetime.strptime(shifts[0]['date'], '%Y-%m-%d')
            current_time = first_date.replace(hour=0, minute=0, second=0, microsecond=0)
        except:
            pass

    # 4. Deterministic Engine Logic
    cronograma_final = []
    tabla_finalizacion = []
    
    for item in backlog_list:
        ref_name = str(item['ref'])
        kg_restantes = item['kg_total']
        
        cap = rewinder_capacities.get(ref_name, {})
        tasa_unitaria = cap.get('kg_per_hour', 0)
        n_maq_operario = cap.get('n_optimo', 1)
        
        if tasa_unitaria == 0: continue 
        
        puestos_activos = 28
        velocidad_planta_rew = tasa_unitaria * puestos_activos
        operarios_reales = math.ceil(puestos_activos / n_maq_operario)
        
        while kg_restantes > 0.01:
            fecha_str = current_time.strftime("%Y-%m-%d")
            horas_disponibles_hoy = 24 - (current_time.hour + current_time.minute/60.0)
            
            duracao_bloque_horas = min(kg_restantes / velocidad_planta_rew, horas_disponibles_hoy)
            kg_producidos_bloque = duracao_bloque_horas * velocidad_planta_rew
            
            inicio_bloque = current_time.strftime("%H:%M")
            current_time = current_time + timedelta(hours=duracao_bloque_horas)
            fin_bloque = "24:00" if duracao_bloque_horas == horas_disponibles_hoy else current_time.strftime("%H:%M")
            
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
            
            dia_entry = next((d for d in cronograma_final if d["fecha"] == fecha_str), None)
            if not dia_entry:
                dia_entry = {
                    "fecha": fecha_str, 
                    "turnos_asignados": [],
                    "requerimiento_abastecimiento": {"kg_totales_demandados": 0, "horas_produccion_conjunta": 0, "detalle_torcedoras": []},
                    "metricas_dia": {"operarios_maximos": 0, "puestos_activos": 28}
                }
                cronograma_final.append(dia_entry)
            
            dia_entry["turnos_asignados"].append({
                "orden_secuencia": len(dia_entry["turnos_asignados"]) + 1,
                "referencia": ref_name,
                "hora_inicio": inicio_bloque,
                "hora_fin": fin_bloque,
                "puestos_utilizados": puestos_activos,
                "operarios_calculados": operarios_reales,
                "kg_producidos": round(kg_producidos_bloque, 2)
            })

            dia_entry["requerimiento_abastecimiento"]["detalle_torcedoras"].extend(detalle_suministro)
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

    # --- NUEVA LÓGICA DE SUMINISTRO (Balance de Masas y Especialización JIT) ---
    # 1. Identificamos la demanda nativa (lo que el Rewinder hace hoy) vs demanda futura
    nativa_dia = {dia["fecha"]: {t["referencia"] for t in dia["turnos_asignados"]} for dia in cronograma_final}
    
    demanda_acumulada = {} # {fecha: {ref: kg_total}}
    for dia in cronograma_final:
        f = dia["fecha"]
        if f not in demanda_acumulada: demanda_acumulada[f] = {}
        for det in dia["requerimiento_abastecimiento"]["detalle_torcedoras"]:
            r = det["ref"]
            demanda_acumulada[f][r] = demanda_acumulada[f].get(r, 0) + det["kg_aportados"]

    # 2. Capacidades y Máquinas
    kgh_lookup = {}
    all_machines = set()
    for denier, data in torsion_capacities.items():
        for m in data.get('machines', []):
            m_id = m['machine_id']
            kgh_lookup[(m_id, denier)] = m['kgh']
            all_machines.add(m_id)

    # 3. Procesamiento en reversa (pumping) con prioridad nativa
    machine_work = {} 
    sorted_dates = sorted(demanda_acumulada.keys(), reverse=True)
    
    for i, fecha_actual in enumerate(sorted_dates):
        if fecha_actual not in machine_work: machine_work[fecha_actual] = {}
        
        # Prioridad 1: Referencias que el Rewinder está haciendo HOY (Native)
        refs_nativas = [r for r in demanda_acumulada[fecha_actual].keys() if r in nativa_dia.get(fecha_actual, set())]
        # Prioridad 2: El resto (referencias pre-bombedas del futuro)
        otras_refs = [r for r in demanda_acumulada[fecha_actual].keys() if r not in nativa_dia.get(fecha_actual, set())]
        
        for ref in (refs_nativas + otras_refs):
            kg_por_cubrir = demanda_acumulada[fecha_actual][ref]
            maquinas_compatibles = sorted([m for m in all_machines if (m, ref) in kgh_lookup])
            
            for m_id in maquinas_compatibles:
                if kg_por_cubrir <= 0.01: break
                if m_id not in machine_work[fecha_actual]:
                    vel = kgh_lookup[(m_id, ref)]
                    if vel <= 0: continue
                    
                    horas_asig = min(24, kg_por_cubrir / vel)
                    kg_asig = horas_asig * vel
                    
                    machine_work[fecha_actual][m_id] = {
                        "ref": f"{ref}", # FIX: No duplicar "Ref"
                        "horas": round(horas_asig, 2),
                        "kg": round(kg_asig, 2)
                    }
                    kg_por_cubrir -= kg_asig
            
            # Overflow al día anterior
            if kg_por_cubrir > 0.01:
                if i + 1 < len(sorted_dates):
                    fecha_previa = sorted_dates[i+1]
                    demanda_acumulada[fecha_previa][ref] = demanda_acumulada[fecha_previa].get(ref, 0) + kg_por_cubrir

    # 4. Re-ensamblamos
    for dia in cronograma_final:
        f = dia["fecha"]
        detalle_final = []
        total_kg_dia = 0
        horas_max_dia = 0
        
        day_loads = machine_work.get(f, {})
        for m_id in sorted(day_loads.keys()):
            work = day_loads[m_id]
            detalle_final.append({
                "maquina": m_id,
                "ref": work["ref"],
                "horas": work["horas"],
                "kg_aportados": work["kg"]
            })
            total_kg_dia += work["kg"]
            horas_max_dia = max(horas_max_dia, work["horas"])
            
        dia["requerimiento_abastecimiento"] = {
            "kg_totales_demandados": round(total_kg_dia, 2),
            "horas_produccion_conjunta": round(horas_max_dia, 2),
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
    comentario = "Estrategia Max-Rewinder: Suministro de Torcedoras especializado y balanceado JIT."
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        try:
            ai_res = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Analista de producción. Resume el plan de abastecimiento y operarios en una frase corta técnica."},
                    {"role": "user", "content": f"Programados {len(backlog_list)} días. Operarios max: {max(dataset_operarios)}. Torsión JIT integrada."}
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
