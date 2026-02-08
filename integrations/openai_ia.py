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

def generate_production_schedule(orders: List[Dict[str, Any]], rewinder_capacities: Dict[str, Dict], total_rewinders: int = 28, shifts: List[Dict[str, Any]] = None, torsion_capacities: Dict[str, Dict] = None, backlog_summary: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Generate a highly detailed operational production schedule using AI based on SPT rule
    and torsion capacity constraints (Continuous Flow Balance).
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return {
            "error": "OPENAI_API_KEY no configurada",
            "scenario": None
        }

    client = OpenAI(api_key=api_key)
    
    # Prepare structured references data
    references_data = []
    
    if backlog_summary:
        # Use pre-calculated backlog summary from UI
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
    else:
        # Fallback calculation if not provided
        denier_backlog = {}
        for o in orders:
            d_name = o.get('deniers', {}).get('name', 'Unknown')
            denier_backlog[d_name] = denier_backlog.get(d_name, 0) + o.get('total_kg', 0)
        
        for d_name, backlog in denier_backlog.items():
            cap_rew = rewinder_capacities.get(d_name, {})
            cap_torsion = torsion_capacities.get(d_name, {}) if torsion_capacities else {}
            
            references_data.append({
                "Ref": d_name,
                "kg_h_rewinder": cap_rew.get('kg_per_hour', 0),
                "N_optimo": cap_rew.get('n_optimo', 0),
                "Capacidad_torsion_total_kgh": cap_torsion.get('total_kgh', 0),
                "Maquinas_torsion_detalle": cap_torsion.get('machines', []),
                "Backlog_kg": backlog
            })
    
    # Prepare calendar data
    calendar_data = []
    if shifts:
        for s in shifts:
            calendar_data.append({
                "fecha": s.get('date'),
                "horas_disponibles": s.get('working_hours', 24)
            })
    
    context_data = {
        "referencias_backlog": references_data,
        "calendario_turnos": calendar_data,
        "restricciones_globales": {
            "puestos_rewinder_totales": total_rewinders
        }
    }
    
    prompt = f"""# ROL: PLANIFICADOR MAESTRO DE PRODUCCIÓN (REWINDER-FIRST STRATEGY)

Eres un motor de optimización para una planta textil. Tu objetivo supremo es MAXIMIZAR LA UTILIZACIÓN DE LOS 28 PUESTOS DE REBOBINADO.

## 🎯 OBJETIVO SUPREMO: "REWINDER-FIRST & ZERO IDLE TIME"
Tu única métrica de éxito es la OCUPACIÓN TOTAL de los 28 puestos de rebobinado.
- **REGLA DE ORO 1:** Mantener SIEMPRE los 28 puestos ocupados mientras haya backlog.
- **REGLA DE ORO 2:** Prohibidos los huecos. Si la producción termina a las 14:00, la siguiente referencia EMPIEZA a las 14:00.
- **REGLA DE ORO 3:** Las torcedoras (T11-T16) NO son el cuello de botella para la programación. Se asume que hay stock o buffer.

## ⚠️ REGLAS CRÍTICAS DE ASIGNACIÓN (PUESTOS)

1. **REFERENCIAS DE ALTO DENIER (>= 12000):**
   - **MANDATO:** ASIGNAR SIEMPRE 28 PUESTOS. 
   - Ignorar cualquier cálculo de capacidad de torcedoras. El objetivo es evacuar el material al máximo ritmo posible.

2. **REFERENCIAS ESTÁNDAR (< 12000):**
   - Prioridad: Intentar asignar 28 puestos.
   - Solo si el balance físico es absurdamente bajo (ej. < 10 puestos) y no hay otra referencia para complementar, podrías reducir, pero la instrucción general es **SATURAR EL RECURSO REWINDER**. 
   - En caso de duda, ASIGNA 28 PUESTOS. Asumimos stock de seguridad en las torcedoras.

## ⚙️ ALGORITMO DE CONTINUIDAD (FLUJO ININTERRUMPIDO)

Genera una "tira de tiempo" lineal y continua:
1. Ordenar backlog por SPT (Shortest Processing Time).
2. Para cada referencia:
   - Calcular `Tasa_Produccion = Puestos_Asignados (28) * Kg_Hora_Rewinder`.
   - `Hora_Fin = Hora_Inicio + (Kg_Pendientes / Tasa_Produccion)`.
   - La `Hora_Inicio` de la siguiente es la `Hora_Fin` de la anterior.
3. Dividir esa tira en días naturales de 24 horas para el JSON.

## 📥 DATOS DE ENTRADA
{json.dumps(context_data, indent=2, ensure_ascii=False)}

## 📤 FORMATO DE SALIDA (JSON ÚNICAMENTE)
Asegúrate de que la suma de horas en `turnos_asignados` por cada día sume exactamente la `horas_disponibles` del calendario (normalmente 24h).

{{
  "resumen_global": {{
    "total_dias_programados": int,
    "fecha_finalizacion_total": "YYYY-MM-DD HH:MM",
    "comentario_estrategia": "Estrategia Max-Rewinder aplicada. Torcedoras operando bajo demanda."
  }},
  "tabla_finalizacion_referencias": [
    {{
      "referencia": int,
      "fecha_finalizacion": "YYYY-MM-DD HH:MM",
      "puestos_promedio": int,
      "kg_totales": float
    }}
  ],
  "cronograma_diario": [
    {{
      "fecha": "YYYY-MM-DD",
      "turnos_asignados": [
        {{
          "orden_secuencia": 1,
          "referencia": int,
          "hora_inicio": "HH:MM",
          "hora_fin": "HH:MM",
          "puestos_utilizados": int,
          "operarios_calculados": int,
          "kg_producidos": float,
          "torcedoras_implicadas": [
             {{"maquina": "T11", "estado": "Activa/Ociosa", "horas_uso": float}}
          ]
        }}
      ]
    }}
  ]
}}

SALIDA = SOLO JSON (sin explicaciones, sin markdown)"""
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Especialista en scheduling industrial. Solo respondes con JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            response_format={"type": "json_object"}
        )
        
        result_text = response.choices[0].message.content
        result = json.loads(result_text)
        
        return {"scenario": result}
        
    except Exception as e:
        return {
            "error": f"Error al procesar la programación: {e}",
            "scenario": None
        }
