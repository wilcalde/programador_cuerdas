from openai import OpenAI
import os
import json
from typing import List, Dict, Any

def get_ai_optimization_scenario(backlog: List[Dict[str, Any]], reports: List[Dict[str, Any]]) -> str:
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
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return {"error": "OPENAI_API_KEY no configurada", "scenario": None}

    client = OpenAI(api_key=api_key)
    
    references_data = []
    if backlog_summary:
        for d_name, data in backlog_summary.items():
            cap_rew = rewinder_capacities.get(d_name, {})
            cap_torsion = torsion_capacities.get(d_name, {}) if torsion_capacities else {}
            references_data.append({
                "Ref": d_name,
                "kg_h_rewinder": cap_rew.get('kg_per_hour', 0),
                "N_optimo": cap_rew.get('n_optimo', 0),
                "Capacidad_torsion_total_kgh": cap_torsion.get('total_kgh', 0),
                "Maquinas_torsion_detalle": cap_torsion.get('machines', []),
                "Backlog_kg": data.get('kg_total', 0)
            })
    
    calendar_data = []
    if shifts:
        for s in shifts:
            calendar_data.append({"fecha": s.get('date'), "horas_disponibles": s.get('working_hours', 24)})
    
    context_data = {
        "referencias_backlog": references_data,
        "calendario_turnos": calendar_data,
        "restricciones_globales": {"puestos_rewinder_totales": total_rewinders}
    }
    
    prompt = f"""# ROL: Especialista en Balance de Líneas de Producción Industrial..."""
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Especialista en scheduling industrial. Solo respondes con JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            response_format={"type": "json_object"}
        )
        result = json.loads(response.choices[0].message.content)
        return {"scenario": result}
    except Exception as e:
        return {"error": str(e), "scenario": None}
