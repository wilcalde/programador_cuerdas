"""
MOTOR DE OPTIMIZACIÓN DE PRODUCCIÓN v5.0
======================================
Sistema de programación de producción de cabuyas (cuerdas) de polipropileno
Planta con 5 máquinas de torsión (T11, T12, T14, T15, T16) y 28 puestos de rewinder

PRINCIPIOS FUNDAMENTALES:
- Ocupación SIEMPRE 95-100% de los 28 puestos rewinder
- Estrategia "Denier Continuo" para minimizar cambios
- Balance Torsión/Rewinder entre 0.95-1.15
- Uso prioritario de T14 para deniers 12000/18000
"""

from openai import OpenAI
import os
import json
from typing import List, Dict, Any, Tuple, Set
import math
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum


class OptimizationStrategy(Enum):
    MAX_OUTPUT = "max_output"  # Maximizar ocupación y producción
    PRIORITY = "priority"      # Priorizar órdenes urgentes


@dataclass
class DenierConfig:
    """Configuración de un denier específico"""
    name: str
    kg_per_hour_rewinder: float
    n_optimo: int
    machines_torsion: List[Dict] = field(default_factory=list)
    
    @property
    def total_kgh_torsion(self) -> float:
        """Capacidad total de torsión para este denier"""
        return sum(m.get('kgh', 0) for m in self.machines_torsion)
    
    @property
    def efficiency_ratio(self) -> float:
        """Ratio de eficiencia: producción rewinder vs torsión"""
        if self.total_kgh_torsion == 0:
            return 0
        return self.kg_per_hour_rewinder / self.total_kgh_torsion


@dataclass
class BacklogItem:
    """Item pendiente en el backlog"""
    ref: str
    descripcion: str
    denier: str
    kg_pendientes: float
    kg_total: float
    is_priority: bool = False
    
    @property
    def progress(self) -> float:
        """Porcentaje de avance (0-1)"""
        if self.kg_total == 0:
            return 0
        return 1 - (self.kg_pendientes / self.kg_total)


@dataclass
class ShiftAssignment:
    """Asignación para un turno específico"""
    denier: str
    posts: int
    operarios: int
    kg_producir: float
    machines_torsion: List[Dict] = field(default_factory=list)
    balance_ratio: float = 0.0


@dataclass
class ShiftResult:
    """Resultado de un turno completo"""
    nombre: str
    horario: str
    assignments: List[ShiftAssignment] = field(default_factory=list)
    posts_ocupados: int = 0
    posts_libres: int = 28
    operarios_totales: int = 0
    kg_total: float = 0.0
    deniers_usados: Set[str] = field(default_factory=set)
    
    @property
    def ocupacion_pct(self) -> float:
        return (self.posts_ocupados / 28) * 100


class MaxOutputOptimizer:
    """
    Optimizador de producción con estrategia de máxima ocupación
    
    OBJETIVOS:
    1. Ocupar 95-100% de los 28 puestos rewinder
    2. Mantener balance Torsión/Rewinder entre 0.95-1.15
    3. Minimizar cambios de denier entre turnos
    4. Priorizar uso de T14 para deniers 12000/18000
    """
    
    # Constantes de configuración
    TOTAL_POSTS = 28
    MIN_OCUPACION_PCT = 95.0
    TARGET_BALANCE_MIN = 0.95
    TARGET_BALANCE_MAX = 1.15
    SHIFT_DURATION_HOURS = 8
    
    # Definición de turnos
    SHIFT_DEFS = [
        {"nombre": "A", "horario": "06:00 - 14:00"},
        {"nombre": "B", "horario": "14:00 - 22:00"},
        {"nombre": "C", "horario": "22:00 - 06:00"},
    ]
    
    def __init__(self, 
                 rewinder_capacities: Dict[str, Dict],
                 torsion_capacities: Dict[str, Dict],
                 strategy: str = 'kg'):
        """
        Inicializa el optimizador
        
        Args:
            rewinder_capacities: Capacidades de rewinder por denier
            torsion_capacities: Capacidades de torsión por denier
            strategy: 'kg' (máxima producción) o 'priority' (prioridades)
        """
        self.rewinder_capacities = rewinder_capacities
        self.torsion_capacities = torsion_capacities
        self.strategy = strategy
        
        # Estado entre turnos
        self.previous_shift_deniers: Set[str] = set()
        self.denier_configs: Dict[str, DenierConfig] = {}
        self._build_denier_configs()
    
    def _build_denier_configs(self):
        """Construye las configuraciones de deniers con sus capacidades"""
        for denier_name, rw_data in self.rewinder_capacities.items():
            torsion_data = self.torsion_capacities.get(denier_name, {})
            
            self.denier_configs[denier_name] = DenierConfig(
                name=denier_name,
                kg_per_hour_rewinder=rw_data.get('kg_per_hour', 0),
                n_optimo=int(rw_data.get('n_optimo', 1)),
                machines_torsion=torsion_data.get('machines', [])
            )
    
    def _generate_valid_post_counts(self, n_optimo: int) -> List[int]:
        """
        Genera conteos válidos de puestos basados en N óptimo
        
        Regla: Cada operario maneja entre min_load y N puestos
        min_load = max(1, ceil(0.8 * N))
        """
        if n_optimo <= 0:
            return []
        
        min_load = max(1, math.ceil(0.8 * n_optimo))
        valid = set()
        
        # Generar combinaciones para hasta 10 operarios
        for num_operarios in range(1, 11):
            min_posts = num_operarios * min_load
            max_posts = num_operarios * n_optimo
            
            if min_posts > self.TOTAL_POSTS:
                break
                
            for posts in range(min_posts, min(max_posts, self.TOTAL_POSTS) + 1):
                valid.add(posts)
        
        return sorted(valid)
    
    def _calculate_optimal_posts(self, denier: str, available_posts: int) -> int:
        """
        Calcula el número óptimo de puestos para un denier
        
        Busca el mayor número válido de puestos que no exceda available_posts
        """
        config = self.denier_configs.get(denier)
        if not config:
            return 0
        
        valid_posts = self._generate_valid_post_counts(config.n_optimo)
        
        # Encontrar el mayor válido que quepa en available_posts
        for posts in sorted(valid_posts, reverse=True):
            if posts <= available_posts:
                return posts
        
        # Si ninguno cabe, tomar el menor válido (al menos algo de producción)
        return valid_posts[0] if valid_posts else 0
    
    def _calculate_production_capacity(self, denier: str, num_posts: int) -> Dict:
        """
        Calcula capacidades de producción para un denier con N puestos
        
        Returns:
            Dict con kg_rewinder, kg_torsion, balance_ratio
        """
        config = self.denier_configs.get(denier)
        if not config:
            return {'kg_rewinder': 0, 'kg_torsion': 0, 'ratio': 0}
        
        # Capacidad rewinder (lo que se puede consumir)
        kg_rewinder = num_posts * config.kg_per_hour_rewinder * self.SHIFT_DURATION_HOURS
        
        # Capacidad torsión (lo que se puede producir)
        kg_torsion = config.total_kgh_torsion * self.SHIFT_DURATION_HOURS
        
        # Balance ratio (rewinder / torsión)
        # > 1: Faltan máquinas de torsión
        # < 1: Sobran máquinas de torsión
        ratio = kg_rewinder / kg_torsion if kg_torsion > 0 else float('inf')
        
        return {
            'kg_rewinder': kg_rewinder,
            'kg_torsion': kg_torsion,
            'ratio': ratio
        }
    
    def _score_denier(self, denier: str, backlog_item: BacklogItem, 
                     is_continuous: bool) -> float:
        """
        Calcula un score para priorizar deniers
        
        Factores:
        - Continuidad (+1000 puntos)
        - Eficiencia (kg/h de rewinder)
        - Backlog pendiente
        - Prioridad del item
        """
        config = self.denier_configs.get(denier)
        if not config:
            return -float('inf')
        
        score = 0.0
        
        # Bonus por continuidad (MUY IMPORTANTE)
        if is_continuous:
            score += 1000
        
        # Eficiencia de producción
        score += config.kg_per_hour_rewinder * 10
        
        # Volumen pendiente
        score += backlog_item.kg_pendientes / 1000
        
        # Prioridad
        if backlog_item.is_priority:
            score += 500
        
        # Bonus por progreso parcial (terminar lo empezado)
        if backlog_item.progress > 0 and backlog_item.progress < 1:
            score += 200
        
        return score
    
    def _find_best_denier_combination(self, 
                                     backlog_items: List[BacklogItem],
                                     posts_target: int = 28) -> List[Dict]:
        """
        Encuentra la mejor combinación de deniers para llenar los puestos objetivo
        
        Algoritmo:
        1. Ordenar deniers por score (continuidad > eficiencia > backlog)
        2. Seleccionar deniers hasta llenar posts_target
        3. Ajustar posts del último denier si es necesario
        """
        if not backlog_items:
            return []
        
        # Calcular scores
        scored_deniers = []
        for item in backlog_items:
            is_continuous = item.denier in self.previous_shift_deniers
            score = self._score_denier(item.denier, item, is_continuous)
            
            scored_deniers.append({
                'item': item,
                'score': score,
                'is_continuous': is_continuous
            })
        
        # Ordenar por score descendente
        scored_deniers.sort(key=lambda x: x['score'], reverse=True)
        
        # Seleccionar deniers
        selected = []
        posts_used = 0
        
        for sd in scored_deniers:
            if posts_used >= posts_target:
                break
            
            item = sd['item']
            available = posts_target - posts_used
            
            # Calcular puestos óptimos
            posts = self._calculate_optimal_posts(item.denier, available)
            
            if posts > 0:
                # Verificar balance
                capacity = self._calculate_production_capacity(item.denier, posts)
                
                # Si el ratio es muy alto (>1.5), quizás necesitemos más torsión
                # o ajustar los puestos
                if capacity['ratio'] > 1.5 and posts > 3:
                    # Intentar con menos puestos para mejorar balance
                    for reduced_posts in sorted(self._generate_valid_post_counts(
                        self.denier_configs[item.denier].n_optimo), reverse=True):
                        if reduced_posts < posts:
                            reduced_capacity = self._calculate_production_capacity(
                                item.denier, reduced_posts)
                            if self.TARGET_BALANCE_MIN <= reduced_capacity['ratio'] <= self.TARGET_BALANCE_MAX:
                                posts = reduced_posts
                                capacity = reduced_capacity
                                break
                
                selected.append({
                    'item': item,
                    'posts': posts,
                    'capacity': capacity,
                    'is_continuous': sd['is_continuous']
                })
                posts_used += posts
        
        # Si quedan puestos libres (>2), intentar llenar con ajustes
        remaining = posts_target - posts_used
        if remaining >= 2 and selected:
            # Intentar aumentar posts del último denier seleccionado
            last = selected[-1]
            extra_posts = self._calculate_optimal_posts(
                last['item'].denier, 
                last['posts'] + remaining
            )
            if extra_posts > last['posts']:
                last['posts'] = extra_posts
                last['capacity'] = self._calculate_production_capacity(
                    last['item'].denier, extra_posts)
                posts_used = sum(s['posts'] for s in selected)
        
        return selected
    
    def _assign_torsion_machines(self, denier: str, 
                                 target_kg: float) -> List[Dict]:
        """
        Asigna TODAS las máquinas de torsión disponibles para un denier
        
        A diferencia de la lógica anterior que asignaba parcialmente,
        esta asigna todas las máquinas para maximizar producción
        """
        config = self.denier_configs.get(denier)
        if not config or not config.machines_torsion:
            return []
        
        assignments = []
        total_kg = 0.0
        
        # Ordenar máquinas por capacidad (descendente)
        machines = sorted(config.machines_torsion, 
                         key=lambda m: m.get('kgh', 0), 
                         reverse=True)
        
        for machine in machines:
            machine_id = machine.get('machine_id', 'Unknown')
            kgh = machine.get('kgh', 0)
            husos = machine.get('husos', 1)
            
            if kgh <= 0:
                continue
            
            kg_turno = kgh * self.SHIFT_DURATION_HOURS
            total_kg += kg_turno
            
            assignments.append({
                'maquina': machine_id,
                'denier': denier,
                'husos_asignados': husos,
                'husos_totales': husos,
                'kgh_maquina': round(kgh, 2),
                'kg_turno': round(kg_turno, 1),
                'operarios': 1
            })
        
        return assignments
    
    def optimize_shift(self, 
                       backlog_items: List[BacklogItem],
                       shift_index: int) -> ShiftResult:
        """
        Optimiza un turno individual
        
        1. Selecciona combinación de deniers para llenar 95-100% de puestos
        2. Asigna todas las máquinas de torsión disponibles
        3. Calcula balance ratio
        4. Actualiza estado para siguiente turno
        """
        shift_def = self.SHIFT_DEFS[shift_index % len(self.SHIFT_DEFS)]
        
        # Filtrar items con backlog real
        active_items = [item for item in backlog_items if item.kg_pendientes > 0.1]
        
        if not active_items:
            return ShiftResult(
                nombre=shift_def['nombre'],
                horario=shift_def['horario'],
                posts_libres=self.TOTAL_POSTS
            )
        
        # Encontrar mejor combinación
        combination = self._find_best_denier_combination(active_items, self.TOTAL_POSTS)
        
        if not combination:
            return ShiftResult(
                nombre=shift_def['nombre'],
                horario=shift_def['horario'],
                posts_libres=self.TOTAL_POSTS
            )
        
        # Crear asignaciones
        assignments = []
        total_posts = 0
        total_operarios = 0
        total_kg = 0
        deniers_used = set()
        
        for combo in combination:
            item = combo['item']
            posts = combo['posts']
            capacity = combo['capacity']
            
            # Calcular operarios necesarios
            config = self.denier_configs[item.denier]
            operarios = math.ceil(posts / config.n_optimo)
            
            # Asignar máquinas de torsión (TODAS las disponibles)
            torsion_machines = self._assign_torsion_machines(item.denier, capacity['kg_torsion'])
            
            # Calcular kg real a producir (mínimo entre capacidad rewinder y backlog)
            kg_a_producir = min(capacity['kg_rewinder'], item.kg_pendientes)
            
            assignment = ShiftAssignment(
                denier=item.denier,
                posts=posts,
                operarios=operarios,
                kg_producir=kg_a_producir,
                machines_torsion=torsion_machines,
                balance_ratio=capacity['ratio']
            )
            
            assignments.append(assignment)
            total_posts += posts
            total_operarios += operarios
            total_kg += kg_a_producir
            deniers_used.add(item.denier)
        
        # Actualizar deniers del turno anterior para continuidad
        self.previous_shift_deniers = deniers_used
        
        return ShiftResult(
            nombre=shift_def['nombre'],
            horario=shift_def['horario'],
            assignments=assignments,
            posts_ocupados=total_posts,
            posts_libres=self.TOTAL_POSTS - total_posts,
            operarios_totales=total_operarios,
            kg_total=total_kg,
            deniers_usados=deniers_used
        )
    
    def generate_schedule(self, 
                         backlog_summary: Dict[str, Any],
                         shifts: List[Dict] = None) -> Dict[str, Any]:
        """
        Genera el cronograma completo de producción
        
        Returns:
            Dict con el escenario completo en formato compatible con la API
        """
        # Convertir backlog_summary a BacklogItems
        backlog_items = []
        if backlog_summary:
            for ref, data in backlog_summary.items():
                if data.get('kg_total', 0) > 0.1:
                    backlog_items.append(BacklogItem(
                        ref=ref,
                        descripcion=data.get('description', ''),
                        denier=data.get('denier', ''),
                        kg_pendientes=float(data['kg_total']),
                        kg_total=float(data['kg_total']),
                        is_priority=data.get('is_priority', False)
                    ))
        
        if not backlog_items:
            return {
                "scenario": {
                    "resumen_global": {
                        "comentario_estrategia": "No hay items en el backlog.",
                        "ocupacion_promedio": 0,
                        "alerta_capacidad": "⚠️ Sin backlog para programar"
                    },
                    "cronograma_diario": []
                }
            }
        
        # Configurar calendario
        start_date = datetime.now() + timedelta(days=1)
        if shifts and len(shifts) > 0:
            try:
                start_date = datetime.strptime(shifts[0]['date'], '%Y-%m-%d')
            except:
                pass
        
        shifts_dict = {s['date']: s['working_hours'] for s in shifts} if shifts else {}
        
        # Simulación de turnos
        cronograma = []
        tabla_finalizacion = {}
        total_kg_inicial = sum(item.kg_pendientes for item in backlog_items)
        ocupaciones = []
        
        current_date = start_date
        max_days = 60  # Límite de seguridad
        
        for day in range(max_days):
            # Verificar si queda backlog
            active_items = [item for item in backlog_items if item.kg_pendientes > 0.1]
            if not active_items:
                break
            
            date_str = current_date.strftime("%Y-%m-%d")
            working_hours = float(shifts_dict.get(date_str, 24))
            num_shifts = int(working_hours // self.SHIFT_DURATION_HOURS)
            
            day_entry = {
                "fecha": date_str,
                "turnos": [],
                "turnos_torsion": [],
                "debug_info": {}
            }
            
            # Procesar cada turno del día
            last_shift_result = None
            for shift_idx in range(num_shifts):
                # Re-filtrar items activos
                current_active = [item for item in backlog_items if item.kg_pendientes > 0.1]
                if not current_active:
                    break
                
                # Optimizar turno
                shift_result = self.optimize_shift(current_active, shift_idx)
                last_shift_result = shift_result
                ocupaciones.append(shift_result.ocupacion_pct)
                
                # Preparar datos de rewinder para respuesta
                rewinder_data = []
                for assign in shift_result.assignments:
                    rewinder_data.append({
                        "referencia": assign.denier,
                        "descripcion": next((i.descripcion for i in current_active 
                                           if i.denier == assign.denier), ''),
                        "denier": assign.denier,
                        "puestos": assign.posts,
                        "operarios": assign.operarios,
                        "kg_producidos": round(assign.kg_producir, 1)
                    })
                    
                    # Actualizar backlog
                    for item in backlog_items:
                        if item.denier == assign.denier:
                            item.kg_pendientes -= assign.kg_producir
                            if item.kg_pendientes <= 0.1 and assign.denier not in tabla_finalizacion:
                                tabla_finalizacion[assign.denier] = {
                                    "referencia": assign.denier,
                                    "descripcion": item.descripcion,
                                    "fecha_finalizacion": f"{date_str} Turno {shift_result.nombre}",
                                    "puestos_promedio": assign.posts,
                                    "kg_totales": item.kg_total
                                }
                            break
                
                # Preparar datos de torsión
                torsion_data = []
                for assign in shift_result.assignments:
                    for machine in assign.machines_torsion:
                        torsion_data.append({
                            **machine,
                            "referencia": assign.denier
                        })
                
                # Agregar turno al día
                day_entry["turnos"].append({
                    "nombre": shift_result.nombre,
                    "horario": shift_result.horario,
                    "operarios_requeridos": shift_result.operarios_totales,
                    "asignaciones": rewinder_data,
                    "posts_ocupados": shift_result.posts_ocupados,
                    "posts_libres": shift_result.posts_libres,
                    "deniers": list(shift_result.deniers_usados)
                })
                
                day_entry["turnos_torsion"].append({
                    "nombre": shift_result.nombre,
                    "horario": shift_result.horario,
                    "operarios_requeridos": len(torsion_data),
                    "asignaciones": torsion_data
                })
            
            # Info de debug
            balance_logs = []
            if last_shift_result and last_shift_result.assignments:
                for assign in last_shift_result.assignments:
                    balance_logs.append({
                        "denier": assign.denier,
                        "balance_ratio": round(assign.balance_ratio, 2),
                        "posts": assign.posts,
                        "kg_rewinder": round(assign.kg_producir, 1),
                        "kg_torsion": round(sum(m['kg_turno'] for m in assign.machines_torsion), 1)
                    })
            
            day_entry["debug_info"] = {
                "balance_torsion": balance_logs,
                "ocupacion_rewinder_avg": f"{last_shift_result.posts_ocupados if last_shift_result else 0} / 28",
                "puestos_libres_promedio": last_shift_result.posts_libres if last_shift_result else 28
            }
            
            cronograma.append(day_entry)
            current_date += timedelta(days=1)
        
        # Calcular métricas finales
        ocupacion_promedio = sum(ocupaciones) / len(ocupaciones) if ocupaciones else 0
        
        # Generar datos para gráfica
        labels = [d['fecha'] for d in cronograma]
        kg_data = []
        ops_data = []
        
        for day in cronograma:
            kg_dia = sum(t['asignaciones'][0]['kg_producidos'] 
                        for t in day['turnos'] if t['asignaciones'])
            kg_data.append(kg_dia)
            
            ops_max = max((t['operarios_requeridos'] for t in day['turnos']), default=0)
            ops_data.append(ops_max)
        
        # Alerta de capacidad
        alerta = "✅ Plan optimizado para máxima ocupación"
        if ocupacion_promedio < self.MIN_OCUPACION_PCT:
            alerta = f"⚠️ Ocupación promedio {ocupacion_promedio:.1f}% - Por debajo del objetivo 95%"
        
        return {
            "scenario": {
                "resumen_global": {
                    "comentario_estrategia": "Estrategia: Máxima Ocupación (95-100%) + Balance Torsión/Rewinder",
                    "fecha_finalizacion_total": cronograma[-1]['fecha'] if cronograma else "N/A",
                    "total_dias_programados": len(cronograma),
                    "kg_totales_plan": round(total_kg_inicial, 1),
                    "ocupacion_promedio": round(ocupacion_promedio, 1),
                    "fecha_capacidad_completa": "Variable",
                    "alerta_capacidad": alerta
                },
                "tabla_finalizacion_referencias": list(tabla_finalizacion.values()),
                "cronograma_diario": cronograma,
                "datos_para_grafica": {
                    "labels": labels,
                    "dataset_kg_produccion": kg_data,
                    "dataset_operarios": ops_data
                }
            }
        }


# ============================================================================
# FUNCIONES DE COMPATIBILIDAD CON API EXISTENTE
# ============================================================================

def generate_production_schedule(
    orders: List[Dict[str, Any]] = None,
    rewinder_capacities: Dict[str, Dict] = None,
    shifts: List[Dict[str, Any]] = None,
    torsion_capacities: Dict[str, Dict] = None,
    backlog_summary: Dict[str, Any] = None,
    strategy: str = 'kg'
) -> Dict[str, Any]:
    """
    Función principal compatible con la API existente
    
    Args:
        orders: Lista de órdenes (para compatibilidad, no se usa directamente)
        rewinder_capacities: Capacidades de rewinder por denier
        shifts: Lista de turnos disponibles
        torsion_capacities: Capacidades de torsión por denier
        backlog_summary: Resumen del backlog a producir
        strategy: Estrategia de optimización ('kg' o 'priority')
    
    Returns:
        Dict con el escenario de producción generado
    """
    # Validar parámetros obligatorios
    if not rewinder_capacities:
        return {
            "scenario": {
                "resumen_global": {
                    "comentario_estrategia": "Error: No se proporcionaron capacidades de rewinder",
                    "alerta_capacidad": "❌ Error: Datos insuficientes"
                },
                "cronograma_diario": []
            }
        }
    
    if not backlog_summary:
        return {
            "scenario": {
                "resumen_global": {
                    "comentario_estrategia": "No hay items en el backlog.",
                    "alerta_capacidad": "⚠️ Sin backlog para programar"
                },
                "cronograma_diario": []
            }
        }
    
    optimizer = MaxOutputOptimizer(
        rewinder_capacities=rewinder_capacities,
        torsion_capacities=torsion_capacities or {},
        strategy=strategy
    )
    
    return optimizer.generate_schedule(
        backlog_summary=backlog_summary,
        shifts=shifts
    )


def get_ai_optimization_scenario(backlog: List[Dict[str, Any]], 
                                 reports: List[Dict[str, Any]]) -> str:
    """
    Genera un escenario de optimización usando IA (GPT-4o-mini)
    
    Args:
        backlog: Lista de items en backlog
        reports: Lista de reportes/novedades
    
    Returns:
        String con el análisis de la IA
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return "Error: OPENAI_API_KEY no configurada."

    client = OpenAI(api_key=api_key)
    
    # Calcular métricas del backlog
    total_kg = sum(item.get('total_kg', 0) for item in backlog)
    num_items = len(backlog)
    
    context = f"""
    Eres un experto en optimización de plantas industriales de producción de cabuyas.
    Actúas como consultor senior para Ciplas.
    
    Datos actuales:
    - Total de items en backlog: {num_items}
    - Total de kg pendientes: {total_kg:,.1f} kg
    - Novedades/reportes: {reports}
    
    Analiza la situación y proporciona recomendaciones específicas para:
    1. Mejorar la ocupación de los 28 puestos rewinder
    2. Balancear la producción entre torsión y rewinder
    3. Minimizar cambios de denier entre turnos
    4. Maximizar el uso de la máquina T14 para deniers 12000/18000
    
    Genera un plan de acción breve y directo (máximo 300 palabras).
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Consultor Senior de Procesos Industriales especializado en manufactura de cabuyas."},
                {"role": "user", "content": context}
            ],
            max_tokens=500
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Error al consultar la IA: {e}"


# ============================================================================
# FUNCIONES UTILITARIAS (MANTENIDAS PARA COMPATIBILIDAD)
# ============================================================================

def _generate_valid_post_sets(n_optimo: int, max_posts: int = 28) -> List[int]:
    """
    Función de compatibilidad hacia atrás
    Genera lista de conteos válidos de puestos basados en N óptimo
    """
    if n_optimo <= 0:
        return []
    
    min_load = math.ceil(0.8 * n_optimo)
    if min_load < 1:
        min_load = 1
    
    valid = set()
    max_operators = max_posts // min_load + 1
    
    for k in range(1, max_operators + 1):
        low = k * min_load
        high = k * n_optimo
        if low > max_posts:
            break
        for p in range(low, min(high, max_posts) + 1):
            valid.add(p)
    
    return sorted(valid)


def assign_shift_greedy(
    active_backlog: List[Dict],
    rewinder_posts_limit: int,
    torsion_capacities: Dict[str, Dict],
    shift_duration: float
) -> Tuple[List[Dict], List[Dict], int]:
    """
    Función de compatibilidad hacia atrás.
    Ahora redirige al nuevo optimizador.
    """
    # Para compatibilidad, devolvemos estructura similar a la anterior
    
    rewinder_assignments = []
    torsion_assignments = []
    posts_remaining = rewinder_posts_limit
    
    # Lógica simplificada de compatibilidad
    for item in active_backlog:
        if posts_remaining <= 0:
            break
        
        ref = item.get('ref', '')
        denier = item.get('denier', '')
        valid_posts = item.get('valid_posts', [1])
        
        # Tomar el mayor válido que quepa
        for p in sorted(valid_posts, reverse=True):
            if p <= posts_remaining:
                rewinder_assignments.append({
                    'ref': ref,
                    'descripcion': item.get('descripcion', ''),
                    'denier': denier,
                    'puestos': p,
                    'operarios': math.ceil(p / item.get('n_optimo', 1)),
                    'kg_producidos': p * item.get('rw_rate', 0) * shift_duration,
                    'rw_rate_total': p * item.get('rw_rate', 0)
                })
                
                # Asignar máquinas de torsión simple
                cap_data = torsion_capacities.get(denier, {})
                for machine in cap_data.get('machines', [])[:2]:  # Limitado para compatibilidad
                    torsion_assignments.append({
                        'maquina': machine.get('machine_id', ''),
                        'denier': denier,
                        'husos_asignados': machine.get('husos', 0),
                        'husos_totales': machine.get('husos', 0),
                        'kgh_maquina': machine.get('kgh', 0),
                        'kg_turno': machine.get('kgh', 0) * shift_duration,
                        'operarios': 1,
                        'ref': ref
                    })
                
                posts_remaining -= p
                break
    
    return rewinder_assignments, torsion_assignments, posts_remaining
