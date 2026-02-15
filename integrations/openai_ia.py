from typing import List, Dict, Any, Tuple
import math
from datetime import datetime, timedelta
from collections import defaultdict
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============================================================================
# CLASES DE DATOS (Estructuras limpias)
# ============================================================================

class TorsionMachine:
    def __init__(self, machine_id: str, denier: int, kgh: float, husos: int = 1):
        self.machine_id = machine_id
        self.denier = denier
        self.kgh = kgh
        self.husos = husos
        self.assigned = False
    
    def __repr__(self):
        return f"TorsionMachine({self.machine_id}, D{self.denier}, {self.kgh}kg/h)"


class RewinderConfig:
    def __init__(self, denier: int, kg_per_hour: float, n_optimo: int):
        self.denier = denier
        self.kg_per_hour = kg_per_hour
        self.n_optimo = n_optimo
    
    def __repr__(self):
        return f"RewinderConfig(D{self.denier}, {self.kg_per_hour}kg/h, N={self.n_optimo})"


class BacklogItem:
    def __init__(self, ref: str, description: str, denier: int, kg_pending: float, 
                 priority: int = 0, client: str = ""):
        self.ref = ref
        self.description = description
        self.denier = denier
        self.kg_pending = kg_pending
        self.kg_initial = kg_pending
        self.priority = priority
        self.client = client
        self.completed = False
        self.completion_date = None
    
    def __repr__(self):
        return f"BacklogItem({self.ref}, D{self.denier}, {self.kg_pending}kg)"


# ============================================================================
# GENERADOR DE SETS VÁLIDOS DE PUESTOS
# ============================================================================

def generate_valid_post_sets(n_optimo: int, max_posts: int = 28) -> List[int]:
    """
    Genera configuraciones válidas de puestos basadas en N óptimo.
    Ej: Si N=5, permite 5, 10, 15... pero también 4-5, 9-10, etc. (±20%)
    """
    if n_optimo <= 0:
        return list(range(1, max_posts + 1))
    
    valid = set()
    min_load = max(1, math.floor(0.8 * n_optimo))
    
    # Generar múltiplos y rangos alrededor de ellos
    for k in range(1, (max_posts // min_load) + 2):
        base = k * n_optimo
        # Permitir ±1 alrededor del múltiplo para flexibilidad
        for p in range(max(1, base - k), min(max_posts, base + k) + 1):
            if p <= max_posts:
                valid.add(p)
    
    # Siempre incluir el mínimo operable
    valid.add(1)
    
    return sorted(valid)


# ============================================================================
# MOTOR DE OPTIMIZACIÓN - ESTRATEGIA "GRUPOS DEDICADOS"
# ============================================================================

class ProductionOptimizer:
    def __init__(self, 
                 torsion_machines: List[TorsionMachine],
                 rewinder_configs: Dict[int, RewinderConfig],
                 total_rewinders: int = 28,
                 shift_hours: float = 8.0):
        
        self.machines = {m.machine_id: m for m in torsion_machines}
        self.rewinder_configs = rewinder_configs
        self.total_rewinders = total_rewinders
        self.shift_hours = shift_hours
        
        # Agrupar máquinas por denier
        self.machines_by_denier = defaultdict(list)
        for m in torsion_machines:
            self.machines_by_denier[m.denier].append(m)
        
        # Calcular capacidades totales por denier
        self.denier_capacity = {}
        for denier, machines in self.machines_by_denier.items():
            self.denier_capacity[denier] = {
                'total_kgh': sum(m.kgh for m in machines),
                'machines_available': len(machines),
                'machine_ids': [m.machine_id for m in machines]
            }
        
        logger.info(f"Optimizer initialized: {len(torsion_machines)} torsion machines, "
                   f"{len(rewinder_configs)} denier configs")
    
    def calculate_production_time(self, kg_needed: float, denier: int, 
                                  num_rewinders: int) -> float:
        """Calcula horas necesarias para producir cierta cantidad"""
        if denier not in self.rewinder_configs:
            return float('inf')
        
        rw_rate = self.rewinder_configs[denier].kg_per_hour
        consumption_rate = num_rewinders * rw_rate
        
        if consumption_rate <= 0:
            return float('inf')
        
        return kg_needed / consumption_rate
    
    def find_optimal_rewinder_count(self, denier: int, torsion_kgh: float, 
                                   available_posts: int) -> Tuple[int, float]:
        """
        Encuentra el número óptimo de rewinders para balancear con torsión.
        Retorna: (num_rewinders, balance_ratio)
        """
        if denier not in self.rewinder_configs:
            return 0, 0.0
        
        rw_config = self.rewinder_configs[denier]
        valid_posts = [p for p in generate_valid_post_sets(rw_config.n_optimo, self.total_rewinders) 
                      if p <= available_posts]
        
        if not valid_posts:
            return 0, 0.0
        
        # Calcular ratio de balance para cada opción
        best_posts = 0
        best_ratio = float('inf')
        
        for posts in valid_posts:
            rw_capacity = posts * rw_config.kg_per_hour
            ratio = abs(torsion_kgh - rw_capacity) / max(torsion_kgh, rw_capacity, 1)
            
            # Preferir ligeramente más capacidad de rewinder que de torsión (1.05-1.15)
            if rw_capacity >= torsion_kgh * 0.95:  # No menos de 95%
                if ratio < best_ratio:
                    best_ratio = ratio
                    best_posts = posts
        
        # Si no encontramos uno >= 95%, tomar el mayor disponible
        if best_posts == 0 and valid_posts:
            best_posts = max(valid_posts)
            rw_capacity = best_posts * rw_config.kg_per_hour
            best_ratio = abs(torsion_kgh - rw_capacity) / max(torsion_kgh, rw_capacity, 1)
        
        balance_ratio = (best_posts * rw_config.kg_per_hour) / max(torsion_kgh, 1)
        return best_posts, balance_ratio
    
    def assign_shift(self, active_backlog: List[BacklogItem], 
                    assigned_machines: set) -> Dict[str, Any]:
        """
        Asigna un turno completo maximizando output y manteniendo balance.
        Estrategia: Agrupar máquinas por denier, priorizar mayor kg pendiente.
        """
        
        # Resetear estado de máquinas no asignadas
        available_machines = []
        for mid, m in self.machines.items():
            if mid not in assigned_machines:
                m.assigned = False
                available_machines.append(m)
        
        if not available_machines:
            return None
        
        # Calcular prioridad de cada denier en backlog
        denier_priority = defaultdict(float)
        for item in active_backlog:
            if item.kg_pending > 0.1:
                # Prioridad = kg pendientes / velocidad de producción
                capacity = self.denier_capacity.get(item.denier, {}).get('total_kgh', 1)
                denier_priority[item.denier] += item.kg_pending / max(capacity, 1)
        
        # Ordenar deniers por prioridad (mayor primero)
        sorted_denierts = sorted(denier_priority.keys(), 
                                key=lambda d: denier_priority[d], 
                                reverse=True)
        
        assignments = []
        posts_remaining = self.total_rewinders
        machines_used_this_shift = set()
        total_kg_planned = 0
        
        # FASE 1: Asignar máquinas completas a deniers prioritarios
        for denier in sorted_denierts:
            if posts_remaining <= 0:
                break
            
            # Verificar si tenemos máquinas para este denier
            available_for_denier = [
                m for m in self.machines_by_denier.get(denier, [])
                if m.machine_id not in assigned_machines and m.machine_id not in machines_used_this_shift
            ]
            
            if not available_for_denier:
                continue
            
            # Calcular capacidad total si usamos TODAS las máquinas disponibles
            total_kgh = sum(m.kgh for m in available_for_denier)
            
            # Encontrar óptimo de rewinders
            optimal_posts, balance_ratio = self.find_optimal_rewinder_count(
                denier, total_kgh, posts_remaining
            )
            
            if optimal_posts <= 0:
                continue
            
            # Calcular kg que podemos producir
            rw_config = self.rewinder_configs.get(denier)
            rw_capacity_kgh = optimal_posts * rw_config.kg_per_hour
            production_kg = min(
                rw_capacity_kgh * self.shift_hours,
                sum(item.kg_pending for item in active_backlog if item.denier == denier)
            )
            
            # Crear asignación
            torsion_assignment = []
            for m in available_for_denier:
                m.assigned = True
                machines_used_this_shift.add(m.machine_id)
                torsion_assignment.append({
                    'machine_id': m.machine_id,
                    'denier': denier,
                    'kgh': m.kgh,
                    'kg_shift': m.kgh * self.shift_hours,
                    'husos': m.husos
                })
            
            # Agrupar items de backlog por denier
            items_for_denier = [item for item in active_backlog 
                              if item.denier == denier and item.kg_pending > 0.1]
            
            ref_codes = [item.ref for item in items_for_denier]
            descriptions = [item.description for item in items_for_denier]
            
            assignment = {
                'denier': denier,
                'references': ref_codes,
                'descriptions': descriptions,
                'torsion_machines': [m.machine_id for m in available_for_denier],
                'torsion_kgh_total': total_kgh,
                'rewinder_posts': optimal_posts,
                'rewinder_operators': math.ceil(optimal_posts / rw_config.n_optimo),
                'balance_ratio': round(balance_ratio, 2),
                'kg_planned': round(production_kg, 1),
                'torsion_details': torsion_assignment,
                'rw_rate_per_post': rw_config.kg_per_hour,
                'rw_total_rate': optimal_posts * rw_config.kg_per_hour
            }
            
            assignments.append(assignment)
            posts_remaining -= optimal_posts
            total_kg_planned += production_kg
            
            logger.info(f"Assigned D{denier}: {len(available_for_denier)} machines, "
                       f"{optimal_posts} rewinders, {production_kg:.1f}kg, "
                       f"balance: {balance_ratio:.2f}")
        
        # FASE 2: Si sobran puestos, intentar asignar a deniers secundarios
        # (Implementación opcional - por ahora mantenemos máquinas dedicadas)
        
        return {
            'assignments': assignments,
            'machines_used': list(machines_used_this_shift),
            'posts_used': self.total_rewinders - posts_remaining,
            'posts_remaining': posts_remaining,
            'total_kg_planned': round(total_kg_planned, 1),
            'efficiency': round((self.total_rewinders - posts_remaining) / self.total_rewinders * 100, 1)
        }


# ============================================================================
# GENERADOR DE CRONOGRAMA COMPLETO
# ============================================================================

def generate_production_schedule(
    orders: List[Dict[str, Any]] = None,
    rewinder_capacities_db: Dict[str, Any] = None,  # Datos de BD
    torsion_capacities_db: Dict[str, Any] = None,   # Datos de BD
    backlog_summary: Dict[str, Any] = None,
    total_rewinders: int = 28,
    shift_hours: float = 8.0,
    shifts_per_day: int = 3,
    max_days: int = 60,
    start_date: datetime = None
) -> Dict[str, Any]:
    """
    Genera cronograma de producción optimizado.
    
    Args:
        orders: Pedidos (opcional, se puede usar backlog_summary)
        rewinder_capacities_db: Configuración de rewinders desde BD
        torsion_capacities_db: Configuración de torsión desde BD
        backlog_summary: Backlog actual desde BD
        total_rewinders: Total de puestos de rewinder disponibles
        shift_hours: Horas por turno
        shifts_per_day: Turnos por día (1-3)
        max_days: Máximo de días a programar
        start_date: Fecha de inicio (default: mañana)
    """
    
    # =====================================================================
    # 1. CARGAR CONFIGURACIONES DESDE BASES DE DATOS (DINÁMICO)
    # =====================================================================
    
    # Parsear configuración de rewinders desde BD
    rewinder_configs = {}
    if rewinder_capacities_db:
        for denier_str, config in rewinder_capacities_db.items():
            try:
                denier = int(denier_str)
                rewinder_configs[denier] = RewinderConfig(
                    denier=denier,
                    kg_per_hour=float(config.get('kg_per_hour', 0)),
                    n_optimo=int(config.get('n_optimo', 1))
                )
            except (ValueError, TypeError) as e:
                logger.warning(f"Error parsing rewinder config for {denier_str}: {e}")
    
    # Parsear configuración de torsión desde BD
    torsion_machines = []
    if torsion_capacities_db:
        for denier_str, config in torsion_capacities_db.items():
            try:
                denier = int(denier_str)
                machines = config.get('machines', [])
                for m in machines:
                    torsion_machines.append(TorsionMachine(
                        machine_id=m.get('machine_id', f'UNKNOWN_{denier}'),
                        denier=denier,
                        kgh=float(m.get('kgh', 0)),
                        husos=int(m.get('husos', 1))
                    ))
            except (ValueError, TypeError) as e:
                logger.warning(f"Error parsing torsion config for {denier_str}: {e}")
    
    if not torsion_machines:
        logger.error("No torsion machines loaded from database")
        return {"error": "No torsion capacity data available"}
    
    if not rewinder_configs:
        logger.error("No rewinder configs loaded from database")
        return {"error": "No rewinder capacity data available"}
    
    logger.info(f"Loaded {len(torsion_machines)} torsion machines")
    logger.info(f"Loaded {len(rewinder_configs)} rewinder configurations")
    
    # =====================================================================
    # 2. CARGAR BACKLOG DESDE BD
    # =====================================================================
    
    backlog_items = []
    if backlog_summary:
        for code, data in backlog_summary.items():
            try:
                kg = float(data.get('kg_total', 0))
                if kg > 0.1:
                    backlog_items.append(BacklogItem(
                        ref=code,
                        description=data.get('description', ''),
                        denier=int(data.get('denier', 0)),
                        kg_pending=kg,
                        priority=int(data.get('priority', 0)),
                        client=data.get('client', '')
                    ))
            except (ValueError, TypeError) as e:
                logger.warning(f"Error parsing backlog item {code}: {e}")
    
    if not backlog_items:
        return {
            "scenario": {
                "resumen_global": {
                    "comentario": "No hay items pendientes en el backlog",
                    "fecha_generacion": datetime.now().isoformat()
                },
                "cronograma_diario": []
            }
        }
    
    logger.info(f"Processing {len(backlog_items)} backlog items")
    
    # =====================================================================
    # 3. INICIALIZAR OPTIMIZADOR
    # =====================================================================
    
    optimizer = ProductionOptimizer(
        torsion_machines=torsion_machines,
        rewinder_configs=rewinder_configs,
        total_rewinders=total_rewinders,
        shift_hours=shift_hours
    )
    
    # =====================================================================
    # 4. GENERAR CRONOGRAMA
    # =====================================================================
    
    if start_date is None:
        start_date = datetime.now() + timedelta(days=1)
    start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    
    cronograma = []
    assigned_machines_global = set()
    completion_tracking = {}
    total_kg_initial = sum(item.kg_initial for item in backlog_items)
    
    current_date = start_date
    
    for day in range(max_days):
        # Verificar si completamos todo
        active_items = [item for item in backlog_items if item.kg_pending > 0.1]
        if not active_items:
            logger.info(f"All items completed on day {day}")
            break
        
        date_str = current_date.strftime("%Y-%m-%d")
        day_entry = {
            "fecha": date_str,
            "turnos": [],
            "resumen_dia": {
                "kg_producidos": 0,
                "items_avanzados": [],
                "items_completados": []
            }
        }
        
        # Generar turnos para el día
        for shift_num in range(shifts_per_day):
            shift_name = chr(ord('A') + shift_num)  # A, B, C...
            shift_start = 6 + (shift_num * 8)  # 06:00, 14:00, 22:00
            shift_end = shift_start + 8
            
            # Ejecutar optimización para este turno
            shift_result = optimizer.assign_shift(active_items, assigned_machines_global)
            
            if not shift_result or not shift_result['assignments']:
                logger.warning(f"No assignments possible for {date_str} Turno {shift_name}")
                continue
            
            # Actualizar backlog y tracking
            kg_produced_this_shift = 0
            for assignment in shift_result['assignments']:
                denier = assignment['denier']
                kg_to_produce = assignment['kg_planned']
                
                # Distribuir producción entre items de este denier
                items_for_denier = [item for item in backlog_items 
                                   if item.denier == denier and item.kg_pending > 0.1]
                
                remaining_for_denier = kg_to_produce
                for item in items_for_denier:
                    if remaining_for_denier <= 0:
                        break
                    
                    actual_production = min(item.kg_pending, remaining_for_denier)
                    item.kg_pending -= actual_production
                    remaining_for_denier -= actual_production
                    kg_produced_this_shift += actual_production
                    
                    # Tracking de finalización
                    if item.kg_pending <= 0.1 and item.ref not in completion_tracking:
                        item.completed = True
                        item.completion_date = f"{date_str} Turno {shift_name}"
                        completion_tracking[item.ref] = {
                            "referencia": item.ref,
                            "descripcion": item.description,
                            "fecha_finalizacion": item.completion_date,
                            "kg_totales": item.kg_initial,
                            "denier": item.denier
                        }
                        day_entry["resumen_dia"]["items_completados"].append(item.ref)
                    
                    if actual_production > 0:
                        day_entry["resumen_dia"]["items_avanzados"].append({
                            "referencia": item.ref,
                            "kg_avance": round(actual_production, 1),
                            "kg_pendiente": round(item.kg_pending, 1)
                        })
            
            # Actualizar máquinas asignadas globalmente (acumulativo)
            assigned_machines_global.update(shift_result['machines_used'])
            
            # Formatear salida del turno
            turno_formatted = {
                "nombre": shift_name,
                "horario": f"{shift_start:02d}:00 - {shift_end:02d}:00",
                "asignaciones": [],
                "estadisticas": {
                    "posts_ocupados": shift_result['posts_used'],
                    "posts_libres": shift_result['posts_remaining'],
                    "eficiencia_rewinder": f"{shift_result['efficiency']}%",
                    "kg_planificados": shift_result['total_kg_planned'],
                    "maquinas_torsion_usadas": len(shift_result['machines_used'])
                }
            }
            
            # Formatear asignaciones detalladas
            for assignment in shift_result['assignments']:
                turno_formatted["asignaciones"].append({
                    "denier": assignment['denier'],
                    "referencias": assignment['references'],
                    "maquinas_torsion": {
                        "ids": assignment['torsion_machines'],
                        "total_kgh": assignment['torsion_kgh_total'],
                        "detalle": assignment['torsion_details']
                    },
                    "rewinders": {
                        "puestos": assignment['rewinder_posts'],
                        "operarios": assignment['rewinder_operators'],
                        "rate_total_kgh": assignment['rw_total_rate'],
                        "balance_ratio": assignment['balance_ratio']
                    },
                    "kg_estimados": assignment['kg_planned']
                })
            
            day_entry["turnos"].append(turno_formatted)
            day_entry["resumen_dia"]["kg_producidos"] += kg_produced_this_shift
        
        # Resumen del día
        day_entry["resumen_dia"]["kg_producidos"] = round(day_entry["resumen_dia"]["kg_producidos"], 1)
        cronograma.append(day_entry)
        current_date += timedelta(days=1)
    
    # =====================================================================
    # 5. ANÁLISIS DE GAPS Y RECOMENDACIONES
    # =====================================================================
    
    gap_analysis = analyze_capacity_gaps(
        optimizer=optimizer,
        backlog_items=backlog_items,
        rewinder_configs=rewinder_configs,
        start_date=start_date,
        days_programmed=len(cronograma)
    )
    
    # =====================================================================
    # 6. PREPARAR RESPUESTA FINAL
    # =====================================================================
    
    # Calcular métricas finales
    total_kg_produced = total_kg_initial - sum(item.kg_pending for item in backlog_items)
    avg_daily_output = total_kg_produced / max(len(cronograma), 1)
    
    # Datos para gráficas
    chart_data = {
        "labels": [day["fecha"] for day in cronograma],
        "kg_produccion": [day["resumen_dia"]["kg_producidos"] for day in cronograma],
        "operarios_rewinder": [
            max((t["rewinders"]["operarios"] for t in day["turnos"][0]["asignaciones"]), default=0)
            if day["turnos"] else 0 
            for day in cronograma
        ],
        "eficiencia": [
            float(day["turnos"][0]["estadisticas"]["eficiencia_rewinder"].replace('%', ''))
            if day["turnos"] else 0
            for day in cronograma
        ]
    }
    
    return {
        "scenario": {
            "resumen_global": {
                "estrategia": "Grupos Dedicados - Máquinas fijas por denier, máximo output",
                "fecha_generacion": datetime.now().isoformat(),
                "fecha_inicio": start_date.strftime("%Y-%m-%d"),
                "fecha_fin_estimada": cronograma[-1]["fecha"] if cronograma else None,
                "dias_programados": len(cronograma),
                "turnos_por_dia": shifts_per_day,
                "kg_totales_inicial": round(total_kg_initial, 1),
                "kg_totales_producidos": round(total_kg_produced, 1),
                "kg_remanentes": round(sum(item.kg_pending for item in backlog_items), 1),
                "promedio_diario_kg": round(avg_daily_output, 1),
                "items_completados": len(completion_tracking),
                "items_pendientes": len([i for i in backlog_items if i.kg_pending > 0.1]),
                "utilizacion_maquinas_torsion": f"{len(assigned_machines_global)}/{len(torsion_machines)} "
                                               f"({len(assigned_machines_global)/len(torsion_machines)*100:.1f}%)"
            },
            "alertas_y_recomendaciones": gap_analysis,
            "tabla_finalizacion": list(completion_tracking.values()),
            "cronograma_diario": cronograma,
            "datos_graficas": chart_data
        }
    }


def analyze_capacity_gaps(optimizer: ProductionOptimizer, 
                         backlog_items: List[BacklogItem],
                         rewinder_configs: Dict[int, RewinderConfig],
                         start_date: datetime,
                         days_programmed: int) -> Dict[str, Any]:
    """
    Analiza gaps de capacidad y genera alertas predictivas.
    """
    
    alerts = []
    recommendations = []
    
    # 1. Analizar deniers subutilizados
    denier_usage = defaultdict(lambda: {'planned': 0, 'capacity': 0})
    
    for item in backlog_items:
        denier_usage[item.denier]['planned'] += item.kg_initial - item.kg_pending
        denier_usage[item.denier]['capacity'] = optimizer.denier_capacity.get(item.denier, {}).get('total_kgh', 0)
    
    # 2. Detectar cuellos de botella
    bottleneck_denierts = []
    for denier, usage in denier_usage.items():
        if usage['capacity'] > 0:
            utilization = usage['planned'] / (usage['capacity'] * days_programmed * 24)
            if utilization > 0.9:
                bottleneck_denierts.append({
                    'denier': denier,
                    'utilizacion': f"{utilization*100:.1f}%",
                    'severidad': 'ALTA' if utilization > 0.95 else 'MEDIA'
                })
    
    if bottleneck_denierts:
        alerts.append({
            "tipo": "CUELLO_DE_BOTELLA",
            "mensaje": f"Deniers con alta utilización: {', '.join(str(b['denier']) for b in bottleneck_denierts)}",
            "detalle": bottleneck_denierts
        })
    
    # 3. Detectar deniers con capacidad ociosa
    idle_machines = []
    for denier, machines in optimizer.machines_by_denier.items():
        used_in_backlog = any(item.denier == denier and item.kg_pending > 0.1 for item in backlog_items)
        if not used_in_backlog:
            total_kgh = sum(m.kgh for m in machines)
            idle_machines.append({
                'denier': denier,
                'maquinas_disponibles': len(machines),
                'capacidad_kgh': total_kgh,
                'capacidad_diaria_kg': total_kgh * 24
            })
    
    if idle_machines:
        # Ordenar por capacidad disponible (mayor primero)
        idle_machines.sort(key=lambda x: x['capacidad_diaria_kg'], reverse=True)
        
        recommendations.append({
            "tipo": "CAPACIDAD_OCIOSA",
            "mensaje": f"Se detectaron {len(idle_machines)} deniers con máquinas disponibles sin backlog asignado",
            "oportunidad": "Agregar pedidos de estas referencias para maximizar output",
            "deniers_disponibles": idle_machines[:3],  # Top 3
            "fecha_sugerida_inclusion": (start_date + timedelta(days=days_programmed//2)).strftime("%Y-%m-%d")
        })
        
        # Calcular cuántos kg adicionales podríamos producir
        additional_capacity = sum(m['capacidad_diaria_kg'] for m in idle_machines)
        recommendations.append({
            "tipo": "POTENCIAL_OUTPUT_ADICIONAL",
            "kg_adicionales_posibles": round(additional_capacity * days_programmed, 1),
            "mensaje": f"Podrías aumentar la producción total en ~{round(additional_capacity * days_programmed, 0)}kg "
                      f"si incluyes pedidos de los deniers ociosos"
        })
    
    # 4. Analizar balance rewinder-torsión
    balance_issues = []
    for denier, config in rewinder_configs.items():
        torsion_cap = optimizer.denier_capacity.get(denier, {}).get('total_kgh', 0)
        rewinder_max_cap = 28 * config.kg_per_hour  # Si usáramos todos los rewinders
        
        if torsion_cap > 0:
            ratio = rewinder_max_cap / torsion_cap
            if ratio < 0.8:
                balance_issues.append({
                    'denier': denier,
                    'problema': 'FALTA_REWINDER',
                    'ratio': round(ratio, 2),
                    'recomendacion': 'Aumentar puestos de rewinder o reducir máquinas de torsión para este denier'
                })
            elif ratio > 1.5:
                balance_issues.append({
                    'denier': denier,
                    'problema': 'EXCESO_REWINDER',
                    'ratio': round(ratio, 2),
                    'recomendacion': 'Redistribuir rewinders a otros deniers o aumentar máquinas de torsión'
                })
    
    if balance_issues:
        alerts.append({
            "tipo": "DESBALANCE_CAPACIDAD",
            "mensaje": f"Se detectaron {len(balance_issues)} desbalances entre torsión y rewinder",
            "issues": balance_issues
        })
    
    # 5. Predicción de fecha de completitud
    pending_items = [item for item in backlog_items if item.kg_pending > 0.1]
    if pending_items:
        avg_daily = sum(item.kg_initial - item.kg_pending for item in backlog_items) / max(days_programmed, 1)
        remaining_kg = sum(item.kg_pending for item in pending_items)
        
        if avg_daily > 0:
            days_remaining = math.ceil(remaining_kg / avg_daily)
            completion_forecast = (start_date + timedelta(days=days_programmed + days_remaining)).strftime("%Y-%m-%d")
            
            recommendations.append({
                "tipo": "FORECAST_COMPLETITUD",
                "fecha_estimada_terminacion_total": completion_forecast,
                "dias_adicionales_requeridos": days_remaining,
                "kg_remanentes": round(remaining_kg, 1)
            })
    
    return {
        "alertas": alerts,
        "recomendaciones": recommendations,
        "indicadores_optimizacion": {
            "maquinas_torsion_total": len(optimizer.machines),
            "deniers_configurados": len(optimizer.denier_capacity),
            "rewinders_configurados": len(rewinder_configs)
        }
    }


# ============================================================================
# FUNCIÓN DE INTEGRACIÓN CON TU APP (Interface)
# ============================================================================

def run_production_optimization(
    db_backlog: Dict[str, Any],
    db_torsion_config: Dict[str, Any],
    db_rewinder_config: Dict[str, Any],
    app_config: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Función principal para integrar con tu aplicación.
    Recibe datos directamente de tus modelos Django/Flask/SQLAlchemy.
    
    Args:
        db_backlog: QuerySet de pedidos pendientes serializados
        db_torsion_config: Configuración de máquinas de torsión desde BD
        db_rewinder_config: Configuración de rewinders desde BD
        app_config: Configuración adicional de la app (turnos, fechas, etc.)
    """
    
    app_config = app_config or {}
    
    # Transformar datos de backlog si vienen en formato diferente
    backlog_summary = {}
    for item in db_backlog.get('items', []):
        code = item.get('code') or item.get('referencia')
        if code:
            backlog_summary[code] = {
                'kg_total': item.get('kg_pendientes', item.get('cantidad', 0)),
                'description': item.get('descripcion', ''),
                'denier': item.get('denier', item.get('titulo', 0)),
                'priority': item.get('prioridad', 0),
                'client': item.get('cliente', '')
            }
    
    # Ejecutar optimización
    result = generate_production_schedule(
        backlog_summary=backlog_summary,
        torsion_capacities_db=db_torsion_config,
        rewinder_capacities_db=db_rewinder_config,
        total_rewinders=app_config.get('total_rewinders', 28),
        shift_hours=app_config.get('shift_hours', 8.0),
        shifts_per_day=app_config.get('shifts_per_day', 3),
        max_days=app_config.get('max_days', 60),
        start_date=app_config.get('start_date')
    )
    
    return result


# ============================================================================
# EJEMPLO DE USO / TEST
# ============================================================================

if __name__ == "__main__":
    # Datos de ejemplo que simularían venir de tu BD
    
    # Configuración de Torsión (de tu primera imagen)
    torsion_db_example = {
        "2000": {
            "machines": [
                {"machine_id": "T11", "kgh": 26.08, "husos": 1},
                {"machine_id": "T12", "kgh": 26.08, "husos": 1},
                {"machine_id": "T15", "kgh": 28.0, "husos": 1},
                {"machine_id": "T16", "kgh": 32.7, "husos": 1}
            ]
        },
        "2500": {
            "machines": [
                {"machine_id": "T11", "kgh": 32.6, "husos": 1},
                {"machine_id": "T12", "kgh": 32.6, "husos": 1},
                {"machine_id": "T15", "kgh": 34.5, "husos": 1},
                {"machine_id": "T16", "kgh": 40.8, "husos": 1}
            ]
        },
        "3000": {
            "machines": [
                {"machine_id": "T11", "kgh": 39.11, "husos": 1},
                {"machine_id": "T12", "kgh": 39.11, "husos": 1},
                {"machine_id": "T15", "kgh": 41.5, "husos": 1},
                {"machine_id": "T16", "kgh": 49.0, "husos": 1}
            ]
        },
        "4000": {
            "machines": [
                {"machine_id": "T11", "kgh": 52.15, "husos": 1},
                {"machine_id": "T12", "kgh": 52.15, "husos": 1}
            ]
        },
        "6000": {
            "machines": [
                {"machine_id": "T11", "kgh": 78.23, "husos": 1},
                {"machine_id": "T12", "kgh": 78.23, "husos": 1}
            ]
        },
        "9000": {
            "machines": [
                {"machine_id": "T15", "kgh": 124.0, "husos": 1},
                {"machine_id": "T16", "kgh": 147.0, "husos": 1}
            ]
        },
        "12000": {
            "machines": [
                {"machine_id": "T14", "kgh": 160.0, "husos": 1},
                {"machine_id": "T15", "kgh": 166.0, "husos": 1},
                {"machine_id": "T16", "kgh": 196.0, "husos": 1}
            ]
        },
        "18000": {
            "machines": [
                {"machine_id": "T14", "kgh": 240.0, "husos": 1}
            ]
        }
    }
    
    # Configuración de Rewinders (de tu segunda imagen)
    rewinder_db_example = {
        "2000": {"kg_per_hour": 5.6, "n_optimo": 9},
        "2500": {"kg_per_hour": 7.2, "n_optimo": 7},
        "3000": {"kg_per_hour": 9.1, "n_optimo": 5},
        "4000": {"kg_per_hour": 11.4, "n_optimo": 4},
        "6000": {"kg_per_hour": 17.0, "n_optimo": 3},
        "9000": {"kg_per_hour": 19.2, "n_optimo": 2},
        "12000": {"kg_per_hour": 21.1, "n_optimo": 2},
        "18000": {"kg_per_hour": 31.0, "n_optimo": 2}
    }
    
    # Backlog de ejemplo (simulando tu imagen 3)
    backlog_example = {
        "CAB00588": {
            "kg_total": 8000.0,
            "description": "CABUYA ECO 4X1 NEGRO",
            "denier": 4000,
            "priority": 1
        },
        "CAB00629": {
            "kg_total": 7036.0,
            "description": "RAFIA PP ECO 6X1K NEGRA",
            "denier": 6000,
            "priority": 2
        },
        "CAB07790": {
            "kg_total": 6000.0,
            "description": "CABUYA MULTIAGRO UV BLAN",
            "denier": 12000,
            "priority": 1
        },
        "CAB04132": {
            "kg_total": 5072.0,
            "description": "CABUYA TOMAT BLANCA UV",
            "denier": 2000,
            "priority": 3
        },
        "CAB04456": {
            "kg_total": 4000.0,
            "description": "CABUYA ECO 12X1K VERDE",
            "denier": 12000,
            "priority": 2
        }
    }
    
    # Ejecutar
    resultado = generate_production_schedule(
        backlog_summary=backlog_example,
        torsion_capacities_db=torsion_db_example,
        rewinder_capacities_db=rewinder_db_example,
        total_rewinders=28,
        shifts_per_day=3,
        max_days=30
    )
    
    # Imprimir resumen
    print("\n" + "="*60)
    print("RESUMEN DE OPTIMIZACIÓN")
    print("="*60)
    resumen = resultado['scenario']['resumen_global']
    for key, value in resumen.items():
        print(f"{key}: {value}")
    
    print("\n" + "="*60)
    print("ALERTAS Y RECOMENDACIONES")
    print("="*60)
    alertas = resultado['scenario']['alertas_y_recomendaciones']
    for alert in alertas['alertas']:
        print(f"⚠️  {alert['tipo']}: {alert['mensaje']}")
    
    for rec in alertas['recomendaciones']:
        print(f"💡 {rec['tipo']}: {rec.get('mensaje', rec.get('oportunidad', ''))}")
    
    print("\n" + "="*60)
    print("CRONOGRAMA (primeros 3 días)")
    print("="*60)
    for day in resultado['scenario']['cronograma_diario'][:3]:
        print(f"\n📅 {day['fecha']}")
        print(f"   KG Producidos: {day['resumen_dia']['kg_producidos']}")
        for turno in day['turnos']:
            print(f"   Turno {turno['nombre']}:")
            for asig in turno['asignaciones']:
                print(f"      D{asig['denier']}: {asig['maquinas_torsion']['ids']} → "
                      f"{asig['rewinders']['puestos']} rewinders "
                      f"(balance: {asig['rewinders']['balance_ratio']})")
