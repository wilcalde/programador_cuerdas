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

## 🎯 OBJETIVO PRINCIPAL: "COPAR LOS REWINDERS"
Tu métrica de éxito es que los 28 puestos de rebobinado estén produciendo el mayor tiempo posible.
- **Prioridad 1:** Mantener los 28 puestos ocupados 24/7.
- **Prioridad 2:** Cumplir con las restricciones físicas de las Torcedoras.
- **Prioridad 3:** Minimizar el backlog según orden SPT.

**NOTA IMPORTANTE:** Se permite (y se espera) que las máquinas Torcedoras (T11-T16) tengan tiempo ocioso si su velocidad es superior a la del rebobinado. No intentes optimizar las torcedoras; optimiza los operarios y puestos de rebobinado.

## ⚠️ REGLAS DE ASIGNACIÓN DE CAPACIDAD

1. **REFERENCIAS DE ALTO DENIER (>= 12000):**
   - **Mandato:** ASIGNAR SIEMPRE 28 PUESTOS.
   - Asumimos que existe buffer o capacidad suficiente. El objetivo es evacuar este material lo más rápido posible.

2. **REFERENCIAS ESTÁNDAR (< 12000):**
   - Calcular capacidad máxima soportada por las torcedoras:
     `Max_Posible = FLOOR(Capacidad_Total_Torcedoras / Tasa_Rebobinado)`
   - **Si Max_Posible >= 28:** ASIGNAR 28 PUESTOS.
   - **Si Max_Posible < 28:** Asignar `Max_Posible` (limitación física real), pero intentar programar inmediatamente otra referencia en los puestos libres si la lógica de tu software lo permite (o asumir ocupación máxima del recurso disponible).

## ⚙️ ALGORITMO DE LLENADO DE DÍAS (TETRIS TEMPORAL)

Debes generar un cronograma continuo. Si una referencia termina a las 10:00 AM, la siguiente DEBE comenzar a las 10:00 AM.

### PASO A PASO:
1. **Inicializar:**
   - `Tiempo_Actual` = Fecha Inicio (ej. 2026-02-09 00:00).
   - `Lista_Pendientes` = Referencias ordenadas por prioridad SPT.

2. **Bucle de Asignación (Mientras exista Backlog):**
   - Tomar la primera referencia de `Lista_Pendientes`.
   - Calcular `Puestos_Activos` (según reglas arriba).
   - Calcular `Tasa_Produccion_Hora` = Puestos_Activos * Kg_Hora_Maquina.
   - Calcular `Duracion_Total_Horas` = Backlog_Kg / Tasa_Produccion_Hora.
   - **Registrar Bloque:**
     - `Inicio` = Tiempo_Actual.
     - `Fin` = Tiempo_Actual + Duracion_Total_Horas.
   - Actualizar `Tiempo_Actual` = `Fin`.
   - Eliminar referencia de la lista y repetir.

3. **Segmentación Diaria (Post-Procesamiento):**
   - Una vez tengas la "tira continua" de tiempo, CORTALA en días de 24 horas (o según disponibilidad).
   - **Ejemplo:** Si la Ref A dura 30 horas y empieza el Día 1 a las 00:00:
     - Día 1: Ref A de 00:00 a 24:00 (28 puestos).
     - Día 2: Ref A de 00:00 a 06:00 (28 puestos) -> **CAMBIO INMEDIATO** -> Ref B de 06:00 a 24:00.

## 📥 DATOS DE ENTRADA
{json.dumps(context_data, indent=2, ensure_ascii=False)}

## 📤 FORMATO DE SALIDA (JSON ESTRUCTURADO - SIN TEXTO ADICIONAL)
Genera UNICAMENTE el siguiente JSON. Asegúrate de que los días estén "llenos" (sin huecos vacíos).

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
