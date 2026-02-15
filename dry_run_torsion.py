# dry_run_torsion.py
from integrations.openai_ia import generate_torsion_schedule

# Mock Data
backlog = {
    "REF-4000-A": {"denier": 4000, "kg_total": 5000, "description": "Big Batch 4k"},
    "REF-6000-B": {"denier": 6000, "kg_total": 3000, "description": "Med Batch 6k"},
    "REF-2000-C": {"denier": 2000, "kg_total": 1000, "description": "Small Batch 2k"},
    "REF-12000-D": {"denier": 12000, "kg_total": 8000, "description": "Huge Batch 12k"},
    "REF-2500-E": {"denier": 2500, "kg_total": 200, "description": "Tiny Batch 2.5k"}
}

torsion_caps = {
    "4000": {"machines": [{"machine_id": "T11", "kgh": 30}, {"machine_id": "T12", "kgh": 30}, {"machine_id": "T16", "kgh": 20}]},
    "6000": {"machines": [{"machine_id": "T11", "kgh": 40}, {"machine_id": "T12", "kgh": 40}]},
    "2000": {"machines": [{"machine_id": "T15", "kgh": 15}, {"machine_id": "T16", "kgh": 10}]},
    "2500": {"machines": [{"machine_id": "T15", "kgh": 18}]},
    "12000": {"machines": [{"machine_id": "T14", "kgh": 80}, {"machine_id": "T16", "kgh": 50}]}
}

print("Running Dry Run...")
result = generate_torsion_schedule(backlog, torsion_caps, max_days=5)

print("\n--- RESUMEN PROGRAMA ---")
print(result['resumen_programa'])

print("\n--- RESUMEN MAQUINAS ---")
for m in result['resumen_maquinas']:
    print(f"{m['maquina']}: {m['horas_trabajadas']}h - {m['kg_totales']}kg ({len(m['referencias'])} refs)")

print("\n--- PRIMEROS 3 TURNOS ---")
for t in result['tabla_turnos'][:3]:
    print(f"{t['fecha']} | Activas: {t['maquinas_activas']} | Kg: {t['total_kg']}")
    for d in t['detalles']:
        print(f"  -> {d['maquina']} [{d['estado']}]: {d['kg']}kg ({d['ref']})")
