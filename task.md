# Productive Ciplas - Task List

## Active Tasks
- [x] Migrate production logic from Google Sheets to Python/Supabase
  - [x] Research Sheet Formulas (MP, TM, Kg/h)
  - [x] Create Supabase Schema (orders, machines_torsion, shifts)
  - [x] Implement deterministic engine in Python
- [x] Implement Operator & Production Graphs (Chart.js)
- [x] Update PDF Export with evolved data
- [x] Sync final evolved logic to GitHub
- [x] Refine Torsion Logic (Quantity Match & Specialization)
  - [x] Implement machine assignment by reference
  - [x] Add recursive pre-pumping to ensure total kilos match
  - [x] Remove mixed reference labels from daily results
  - [x] Verify Torsion total Kg == Rewinder total Kg
  - [x] Implement Universal Machine Compatibility (Mirror mode)
- [x] Debug Vercel 500 Error (Imports & Packages)
- [x] Implement HR Load Balancing & Line Smoothing
  - [x] Refactor engine to support 'Concurrent References' (splitting 28 posts)
  - [x] Implement target operator count (e.g., max 10-12 per shift)
  - [x] Verify operator graph is 'smoother' (fewer extreme peaks)
  - [x] Synchronize Torsion supply with split-production logic
  - [x] Correct OpenAI import source in app.py
  - [x] Add missing __init__.py files
  - [x] Slim down requirements.txt

## Future / On-Hold
- [ ] Integration with historical consumption logs
- [ ] Predictive maintenance alerts via OpenAI
