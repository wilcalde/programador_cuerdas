# HR Load Balancing & Production Smoothing

Refactor the deterministic scheduling engine to calculate collateral resource requirements and HR data for visualization.

## Proposed Changes

### AI Integration
#### [MODIFY] openai_ia.py
- [DONE] Refine Torsion supply calculation using machine-specific speeds from `torsion_capacities`.
- [DONE] Calculate `kg_aportados` per machine in the daily supply detail.
- [DONE] Correct `horas_produccion_conjunta` metric to reflect synchronized pumping time.
- [DONE] Update final JSON structure to match user's specification.

### Torsion Supply Optimization (Machine Specialization)
#### [MODIFY] openai_ia.py
- **Mass Balance**: Ensure `Total Torsion Kg == Total Rewinder Kg` by tracking cumulative demand and supply.
- **Machine Specialization**: Refactor allocation to assign one reference per machine per day.
- **Recursive Pre-pumping**: If a machine lacks capacity for today's demand, the excess is moved to the *same machine* on the previous day.
- **Universal Compatibility**: Machines without explicit denier config now act as universal fallbacks. (MIRROR MODE)

### HR Load Balancing & Line Smoothing
#### [MODIFY] openai_ia.py
- **Concurrent Production**: The engine now splits the 28 posts into two 14-post streams when a high-denier (heavy HR) reference is encountered.
- **Peak Shaving**: Targets a stable operator count (~11) rather than jumping to 14.
- **Segregation**: Heavy references are spread over more days with less simultaneous density.

## Verification Plan
### Automated Verification
- Run several scheduling scenarios to verify:
  - Torsion hours are proportional to Kg produced.
  - `datos_para_grafica` datasets have matching lengths.
  - HR Graph shows "smoother" curves for 18000 denier.
