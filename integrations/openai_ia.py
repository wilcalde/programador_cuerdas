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
    
    prompt = f"""# ROL: Especialista en Balance de Líneas de Producción Industrial

Eres un sistema de optimización que gestiona dos procesos interdependientes:
1. Torcedoras (T11-T16): Producen materia prima a tasas específicas por referencia.
2. Rebobinado (28 puestos): Procesa materia prima con restricciones de operarios (N).

Tu tarea es generar un cronograma SIN CUELLOS DE BOTELLA donde:
- ✅ La producción de rebobinado NUNCA exceda la capacidad de las torcedoras.
- ✅ Se mantenga FLUJO CONSTANTE (sin tiempos muertos en ninguna línea).
- ✅ Se prioricen referencias con mayor tasa kg/h (SPT) para minimizar flow time.
- ✅ Se priorice que los rewinder trabajen la mayor parte del tiempo posible.

##  METODOLOGÍA: "BALANCE DE FLUJO CONTINUO CON RESTRICCIÓN DE TORCEDORAS"

### Paso 1: Calcular capacidad máxima de rebobinado por referencia
Para cada referencia con backlog > 0:
- Capacidad_torcedora = Capacidad_torsion_total_kgh
- Capacidad_rebobinado_teorica = Capacidad_torcedora / kg_h_rewinder
- Max_puestos_permitidos = MIN(Capacidad_rebobinado_teorica, 28)

### Paso 2: Validar flujo continuo (REGLA CRÍTICA)
Producción_rebobinado (Puestos * kg_h_rewinder) <= Producción_torcedora (Capacidad_torsion_total_kgh)
Si se viola -> recalcular puestos.

### Paso 3: Algoritmo de asignación con balance
PARA CADA DÍA con horas disponibles > 0:
  a. Iniciar con referencia de mayor tasa (SPT - kg_h_rewinder).
  b. Asignar el MÁXIMO de puestos posibles (según Paso 1).
  c. Si sobran puestos -> asignar a siguiente prioridad en el mismo bloque si es posible.
  d. Calcular:
      - Horas_reales_bloque = Min(Horas_disponibles_dia, Backlog_restante / (Puestos_asignados * kg_h_rewinder))
      - Horas_torcedoras = (Puestos_asignados * kg_h_rewinder * Horas_reales_bloque) / Capacidad_torsion_total_kgh (Distribuido proporcionalmente en las máquinas de torsión disponibles).

## 🔢 DATOS DE ENTRADA
{json.dumps(context_data, indent=2, ensure_ascii=False)}

## 📤 FORMATO DE SALIDA OBLIGATORIO (JSON)
Genera EXCLUSIVAMENTE este JSON (sin texto adicional):

{{
  "resumen_ejecutivo": {{
    "makespan_final": "YYYY-MM-DD HH:MM",
    "total_horas_máq_requeridas": val,
    "eficiencia_flujo": "100%"
  }},
  "cronograma_diario": [
    {{
      "dia_calendario": "YYYY-MM-DD",
      "horas_disponibles": val,
      "bloques_asignacion": [
        {{
          "bloque_inicio_hora": "HH:MM",
          "bloque_fin_hora": "HH:MM",
          "referencia": "id",
          "puestos_rebobinado": val,
          "operarios_necesarios": val,
          "torcedoras_utilizadas": ["id1", "id2"],
          "horas_torcedoras": [
            {{"torcedora": "id", "horas": val, "tasa_kg_h": val}}
          ],
          "kg_producidos": val,
          "backlog_restante": val
        }}
      ]
    }}
  ],
  "validaciones": {{
    "flujo_constante": "PASSED",
    "sin_tiempo_ocioso": "PASSED",
    "restriccion_torcedoras": "PASSED"
  }}
}}

## ⚠️ REGLAS DE EJECUCIÓN OBLIGATORIAS
1. NUNCA asignar más puestos de rebobinado que la capacidad de torcedoras.
2. SIEMPRE calcular horas de torcedoras basándose en el consumo real del rebobinado.
3. MANTENER flujo continuo: un bloque debe empezar exactamente donde termina el anterior.
4. PRIORIZAR que el proceso de Rebobinado (Rewinders) esté ocupado el máximo tiempo.

SALIDA = SOLO JSON (sin explicaciones, sin markdown)"""
    
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
        
        result_text = response.choices[0].message.content
        result = json.loads(result_text)
        
        return {"scenario": result}
        
    except Exception as e:
        return {
            "error": f"Error al procesar la programación: {e}",
            "scenario": None
        }
