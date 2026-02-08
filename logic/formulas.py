import math

def get_kgh_torsion(denier: float, rpm: int, torsiones_metro: int, husos: int, oee: float = 0.8, desperdicio: float = 0.03) -> float:
    """
    Calculates Torsion Capacity in Kg/h.
    
    Formula:
    v_salida = rpm / torsiones_metro
    kgh = (v_salida * (denier / 9000) * husos * 0.06) * oee * (1 - desperdicio)
    """
    if torsiones_metro == 0:
        return 0.0
    
    # Velocidad de salida (metros/minuto)
    v_salida = rpm / torsiones_metro
    
    # Producción neta por hora (Kg/h)
    # 0.06 is conversion factor for minutes to hours and potential unit adjustments (g to kg / 1000 * 60)
    kgh = (v_salida * (denier / 9000) * husos * 0.06) * oee * (1 - desperdicio)
    return kgh

def get_n_optimo_rew(tm_minutos: float, mp_segundos: float = 37) -> float:
    """
    Calculates optimal machine assignment for Rewinder (Interference model).
    
    Calcula el número óptimo de máquinas por operario (punto de saturación) en el proceso Rewinder.
    
    Args:
        tm_minutos: Tiempo de máquina (ciclo) en minutos - tiempo en que se rebobina un rollo
        mp_segundos: Tiempo de máquina parada (segundos) - tiempo para cambiar rollo o empalmar bobina
    
    Returns:
        N: Número óptimo de máquinas por operario (punto de saturación)
    """
    mp_min = mp_segundos / 60
    # N = Máquinas por operario (Punto de saturación)
    n_optimo = (mp_min + tm_minutos) / mp_min
    return math.floor(n_optimo)

def get_rafia_input(kg_objetivo: float, desperdicio: float = 0.03) -> float:
    """
    Calculates required raw material (Rafia) to meet objective.
    """
    if desperdicio >= 1:
        return kg_objetivo
    return kg_objetivo / (1 - desperdicio)
