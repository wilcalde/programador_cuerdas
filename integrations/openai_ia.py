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
    # For now, we follow the order as they came or simple ascending Ref
    backlog_list.sort(key=lambda x: str(x['ref']))

    # 2. Master Data Lookup
    # Note: rewinder_capacities is already keyed by denier name from app.py
    
    # 3. Calendar Setup
    default_start_date = datetime.now() + timedelta(days=1)
    current_time = default_start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    
    if shifts and len(shifts) > 0:
        # Use first available shift date as start
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
        
        # OBTENER DATOS TÉCNICOS REWINDER
        cap = rewinder_capacities.get(ref_name, {})
        tasa_unitaria = cap.get('kg_per_hour', 0)
        n_maq_operario = cap.get('n_optimo', 1)
        
        if tasa_unitaria == 0: continue 
        
        # REGLA SAGRADA: COPAR REWINDER (28 PUESTOS)
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
            # Las torcedoras deben trabajar para reponer kg_producidos_bloque
            t_cap = torsion_capacities.get(ref_name, {})
            vel_total_torsion = t_cap.get('total_kgh', 50) # Use 50 as fallback if no data
            if vel_total_torsion <= 0: vel_total_torsion = 50
            
            # Horas que el GRUPO de torcedoras debe trabajar para este tramo
            horas_torsion_suministro = kg_producidos_bloque / vel_total_torsion
            
            detalle_suministro = []
            for m in t_cap.get('machines', [{"machine_id": "T-Gen", "kgh": vel_total_torsion}]):
                detalle_suministro.append({
                    "maquina": m['machine_id'],
                    "horas": round(horas_torsion_suministro, 2),
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

            # Acumular requerimiento de abastecimiento del día
            dia_entry["requerimiento_abastecimiento"]["kg_totales_demandados"] += kg_producidos_bloque
            # Nota: las horas de producción conjunta es el máximo de horas de suministro de las refs del día?
            # En este caso, para simplificar según prompt: acumulamos detalles
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

    # Calcular horas_produccion_conjunta diaria final
    for dia in cronograma_final:
        dia["requerimiento_abastecimiento"]["kg_totales_demandados"] = round(dia["requerimiento_abastecimiento"]["kg_totales_demandados"], 2)
        # Horas conjuntas es la duración total del trabajo de torcedoras ese día
        # Según lógica: Kg_Dia / Cap_Conjunta_Promedio_Dia? Simplifiquemos a suma de horas por bloque
        unique_refs = {d['ref'] for d in dia["requerimiento_abastecimiento"]["detalle_torcedoras"]}
        # Realizamos un cálculo más preciso para el reporte
        dia["requerimiento_abastecimiento"]["horas_produccion_conjunta"] = round(sum(t['horas'] for t in dia["requerimiento_abastecimiento"]["detalle_torcedoras"]) / max(len(unique_refs), 1), 2)

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
    comentario = "Estrategia Max-Rewinder: Suministro de Torcedoras calculado para abastecer demanda diaria."
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        client = OpenAI(api_key=api_key)
        try:
            ai_res = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Analista de producción. Resume el plan de abastecimiento y operarios en una frase corta técnica."},
                    {"role": "user", "content": f"Programados {len(backlog_list)} días. Operarios max: {max(dataset_operarios)}. Abastecimiento T11-T16 sincronizado."}
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
