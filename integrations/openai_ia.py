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
    
    prompt = f"""# ROL: ESPECIALISTA EN BALANCE DE LÍNEAS DE PRODUCCIÓN INDUSTRIAL

Eres un sistema de optimización que gestiona DOS procesos interdependientes en una planta de hilados:
1. **TORCEDORAS (T11-T16)**: 5 máquinas que producen materia prima con tasas específicas por referencia (kg/h)
2. **REBOBINADO**: 28 puestos de trabajo operados por personas (parámetro N = máximo puestos por operario)

Tu misión: Generar un CRONOGRAMA EJECUTABLE día a día que:
✅ Elimine TODO el backlog en el mínimo tiempo posible (makespan óptimo)
✅ Mantenga FLUJO CONTINUO: producción de rebobinado ≤ capacidad de torcedoras (nunca exceder)
✅ Evite TIEMPO OCIOSO en ambas líneas (rebobinado siempre trabajando)
✅ Respete restricciones de operarios: Operarios_necesarios = ceil(Puestos_asignados / N)
✅ Priorice referencias por SPT (mayor kg/h de rebobinado primero) para minimizar flow time
✅ Especifique HORAS EXACTAS de cambio de referencia (formato HH:MM) sin ambigüedades

## 🔑 DATOS DE ENTRADA (USAR EXACTAMENTE ESTOS)
{json.dumps(context_data, indent=2, ensure_ascii=False)}

## ⚙️ ALGORITMO DE ASIGNACIÓN (EJECUTAR EN ORDEN ESTRICTO)

### Paso 1: Pre-cálculo de capacidades máximas por referencia
PARA CADA referencia:
Capacidad_torcedora_total = SUMA(tasas_kg/h de torcedoras compatibles) 
Max_puestos_rebobinado = MIN(28, floor(Capacidad_torcedora_total / kg/h_rebobinado)) 
Operarios_minimos = ceil(Max_puestos_rebobinado / N)

### Paso 2: Inicialización
- Hora_acumulada = 0.0 (horas desde inicio el 2026-02-09 00:00)
- Orden_prioridad = (SPT: mayor kg/h primero)

### Paso 3: Bucle de asignación día a día
PARA CADA DÍA con Horas_disponibles > 0:
  a. Hora_inicio_dia = Hora_acumulada
  b. Hora_fin_dia = Hora_inicio_dia + Horas_disponibles
  c. MIENTRAS Hora_acumulada < Hora_fin_dia Y existan referencias con backlog > 0:
      i. Seleccionar siguiente referencia en Orden_prioridad con backlog > 0
      ii. Obtener Max_puestos para esta referencia (Paso 1)
      iii. Calcular horas_necesarias = Backlog[ref] / (Max_puestos × kg/h_rebobinado)
      iv. Si horas_necesarias ≤ (Hora_fin_dia - Hora_acumulada):
           - Asignar bloque completo:
             * Hora_inicio_bloque = Hora_acumulada
             * Hora_fin_bloque = Hora_acumulada + horas_necesarias
             * Backlog[ref] = 0
             * Hora_acumulada = Hora_fin_bloque
      v. Si horas_necesarias > (Hora_fin_dia - Hora_acumulada):
           - Asignar bloque parcial:
             * Hora_inicio_bloque = Hora_acumulada
             * Hora_fin_bloque = Hora_fin_dia
             * Kg_producidos = Max_puestos × kg/h_rebobinado × (Hora_fin_dia - Hora_acumulada)
             * Backlog[ref] -= Kg_producidos
             * Hora_acumulada = Hora_fin_dia → SALIR bucle interno (día terminado)

### Paso 4: Calcular horas de torcedoras para CADA bloque
PARA CADA bloque asignado:
Kg_a_producir = Max_puestos × kg/h_rebobinado × Horas_bloque 
PARA CADA torcedora compatible: Horas_torcedora = Kg_a_producir × (Tasa_torcedora / Capacidad_torcedora_total)

### Paso 5: Validaciones OBLIGATORIAS (ejecutar antes de salida)
✅ Check 1: Para TODO bloque → Producción_rebobinado ≤ Capacidad_torcedoras
✅ Check 2: NO hay tiempo ocioso entre bloques (Hora_fin_bloque_n = Hora_inicio_bloque_n+1)
✅ Check 3: Backlog final = 0 para TODAS las referencias
✅ Check 4: Makespan_final = Total_horas_máq_requeridas / Puestos_promedio (±2% tolerancia)
✅ Check 5: Operarios_necesarios ≤ Operarios_disponibles (asumir ≥10 operarios si no especificado)

## 📤 FORMATO DE SALIDA OBLIGATORIO (JSON ESTRUCTURADO - SIN TEXTO ADICIONAL)

{{
  "resumen_ejecutivo": {{
    "makespan_total_horas": val,
    "makespan_fecha_hora_final": "YYYY-MM-DD HH:MM",
    "total_kg_producidos": val,
    "eficiencia_flujo": "100%",
    "operarios_maximos_requeridos": val
  }},
  "tabla_finalizacion_referencias": [
    {{
      "referencia": "id",
      "backlog_inicial_kg": val,
      "fecha_finalizacion": "YYYY-MM-DD",
      "hora_dia_finalizacion": "HH:MM",
      "horas_acumuladas_desde_inicio": val,
      "puestos_utilizados": val,
      "operarios_utilizados": val
    }}
  ],
  "cronograma_diario": [
    {{
      "dia_calendario": "YYYY-MM-DD",
      "horas_disponibles": val,
      "bloques_asignacion": [
        {{
          "bloque_numero": val,
          "referencia": "id",
          "hora_inicio_dia": "HH:MM",
          "hora_fin_dia": "HH:MM",
          "horas_bloque": val,
          "puestos_rebobinado": val,
          "operarios_necesarios": val,
          "torcedoras_utilizadas": [
            {{"torcedora": "id", "horas_operacion": val, "kg_producidos": val}}
          ],
          "kg_producidos_bloque": val,
          "backlog_restante_post_bloque": val
        }}
      ],
      "estado_fin_dia": {{
        "backlog_total_restante_kg": val,
        "referencias_pendientes": ["id1", "id2"]
      }}
    }}
  ],
  "validaciones_ejecutadas": {{
    "check1_flujo_continuo": "PASSED",
    "check2_sin_tiempo_ocioso": "PASSED",
    "check3_backlog_cero": "PASSED",
    "check4_makespan_optimo": "PASSED",
    "check5_restriccion_operarios": "PASSED"
  }}
}}

## ⚠️ REGLAS DE ORO (VIOLAR = FALLA CRÍTICA)
1. NUNCA asignar más puestos de rebobinado que: floor(Capacidad_torcedoras / kg/h_rebobinado)
2. SIEMPRE especificar hora_inicio_dia y hora_fin_dia en formato HH:MM (ej.: "15:22")
3. NUNCA dejar horas disponibles sin asignar (si el día tiene 24h y terminas a las 15:00 → asignar otra referencia hasta las 24:00)
4. SIEMPRE calcular horas de torcedoras con fórmula exacta (no aproximar)
5. SI cualquier validación falla → RECHAZAR salida y RECALCULAR con temperatura=0.3

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
