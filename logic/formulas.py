import math

def get_kgh_torsion(denier: float, rpm: int, torsiones_metro: int, husos: int, oee: float = 0.8, desperdicio: float = 0.03) -> float:
    if torsiones_metro == 0:
        return 0.0
    v_salida = rpm / torsiones_metro
    kgh = (v_salida * (denier / 9000) * husos * 0.06) * oee * (1 - desperdicio)
    return kgh

def get_n_optimo_rew(tm_minutos: float, mp_segundos: float = 37) -> float:
    mp_min = mp_segundos / 60
    n_optimo = (mp_min + tm_minutos) / mp_min
    return math.floor(n_optimo)

def get_rafia_input(kg_objetivo: float, desperdicio: float = 0.03) -> float:
    if desperdicio >= 1: return kg_objetivo
    return kg_objetivo / (1 - desperdicio)
