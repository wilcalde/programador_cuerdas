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
        
        # OBTENER DATOS TÉCNICOS
        cap = rewinder_capacities.get(ref_name, {})
        tasa_unitaria = cap.get('kg_per_hour', 0)
        n_maq_operario = cap.get('n_optimo', 1)
        
        if tasa_unitaria == 0: continue # Skip if no data
        
        # REGLA SAGRADA: COPAR REWINDER (28 PUESTOS)
        puestos_activos = 28
        velocidad_planta = tasa_unitaria * puestos_activos # Kg/h totales
        operarios_reales = math.ceil(puestos_activos / n_maq_operario)
        
        # Calcular duración total
        horas_necesarias_total = kg_restantes / velocidad_planta
        
        # Loop de segmentación por días (Tetris)
        ref_start_time = current_time
        
        while kg_restantes > 0.01: # Margin for float
            fecha_str = current_time.strftime("%Y-%m-%d")
            
            # Horas restantes hoy (límite 24:00)
            horas_disponibles_hoy = 24 - (current_time.hour + current_time.minute/60.0)
            
            # Tasa de producción actual
            duracion_bloque_horas = min(kg_restantes / velocidad_planta, horas_disponibles_hoy)
            kg_producidos_bloque = duracion_bloque_horas * velocidad_planta
            
            inicio_bloque = current_time.strftime("%H:%M")
            current_time = current_time + timedelta(hours=duracion_bloque_horas)
            fin_bloque = "24:00" if duracion_bloque_horas == horas_disponibles_hoy else current_time.strftime("%H:%M")
            
            # Buscar día en cronograma_final o crearlo
            dia_entry = next((d for d in cronograma_final if d["fecha"] == fecha_str), None)
            if not dia_entry:
                dia_entry = {"fecha": fecha_str, "turnos_asignados": []}
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
            
            kg_restantes -= kg_producidos_bloque
            
            # Si hemos llegado al final del día (24:00), avanzar a la 00:00 del día siguiente
            if fin_bloque == "24:00":
                current_time = (current_time + timedelta(minutes=1)).replace(hour=0, minute=0, second=0, microsecond=0)

        # Registro de finalización por referencia
        tabla_finalizacion.append({
            "referencia": ref_name,
            "fecha_finalizacion": (current_time - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M"),
            "puestos_promedio": puestos_activos,
            "kg_totales": round(item['kg_total'], 2)
        })

    # 5. AI Commentary (Optional/Consultancy)
    comentario = "Estrategia Max-Rewinder Determinista: Ocupación 100% (28 puestos). Flujo continuo sin huecos."
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        client = OpenAI(api_key=api_key)
        try:
            # Solo pedimos un comentario corto para no arriesgar los datos matemáticos
            ai_res = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Analista de producción. Resume la estrategia aplicada en una frase corta (Estrategia Rewinder-First). NO des fechas ni kg."},
                    {"role": "user", "content": f"He programado {len(backlog_list)} referencias copando los 28 puestos de rewinder. El fin total es {current_time.strftime('%Y-%m-%d %H:%M')}."}
                ],
                max_tokens=60
            )
            comentario = ai_res.choices[0].message.content
        except:
            pass

    # 6. Build final JSON structure
    total_dias = len(cronograma_final)
    resultado = {
        "scenario": {
            "resumen_global": {
                "total_dias_programados": total_dias,
                "fecha_finalizacion_total": (current_time - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M"),
                "comentario_estrategia": comentario
            },
            "tabla_finalizacion_referencias": tabla_finalizacion,
            "cronograma_diario": cronograma_final
        }
    }
    
    return resultado
