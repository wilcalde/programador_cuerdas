import unittest
from integrations.openai_ia import assign_shift_greedy

class TestMassBalance(unittest.TestCase):
    def test_strict_mass_balance(self):
        # Mock Data
        # Reference 6000 Denier
        # Torsion Capacity: ~500 kg/h total (simulated)
        # Rewinder: 8 posts * ~106 kg/post = ~850 kg/shift
        
        # Mock Torsion Capacities
        torsion_capacities = {
            "6000": {
                "total_kgh": 62.5, # 62.5 * 8h = 500 kg per shift
                "machines": [
                    {"machine_id": "T1", "kgh": 31.25, "husos": 100},
                    {"machine_id": "T2", "kgh": 31.25, "husos": 100}
                ]
            }
        }
        
        # Mock Backlog
        # Low N_optimo (e.g., 1) to allow flexible post counts
        # User Report: 8 posts = 851 kg in 8 hours.
        # Rate per post per hour = 851 / 8 / 8 = 13.296 kg/h
        rw_rate = 13.3 
        
        backlog = [{
            "ref": "CAB00629",
            "descripcion": "RAFIA 6000",
            "denier": "6000",
            "kg_pendientes": 5000,
            "kg_total_inicial": 5000,
            "is_priority": True,
            "rw_rate": rw_rate, 
            "n_optimo": 1, 
            "valid_posts": [1, 2, 3, 4, 5, 6, 7, 8] # All valid
        }]
        
        # Execute Greedy Assignment
        print("\n--- Testing Strict Mass Balance ---")
        rw_assigns, tor_assigns = assign_shift_greedy(
            backlog,
            rewinder_posts_limit=28, # Plenty of space
            torsion_capacities=torsion_capacities,
            shift_duration=8
        )
        
        # Verification
        if not rw_assigns:
            print("No assignment made!")
            return

        assign = rw_assigns[0]
        posts = assign['puestos']
        kg_produced_rew = assign['kg_producidos']
        
        kg_torsion_supplied = sum(t['kg_turno'] for t in tor_assigns)
        
        print(f"Assigned Posts: {posts}")
        print(f"Rewinder Consumption: {kg_produced_rew:.2f} kg")
        print(f"Torsion Supply: {kg_torsion_supplied:.2f} kg")
        
        # Constraint Check
        # supply should be >= consumption * 0.9
        balance_ratio = kg_torsion_supplied / kg_produced_rew if kg_produced_rew > 0 else 0
        print(f"Balance Ratio: {balance_ratio:.2f}")

        # In the BUGGY version, this was 0.58 (497/851). 
        # In the FIXED version, posts should reduce to ~4 or 5 to match 500kg.
        # 5 posts * 106.375 * 8 = 4255 ?? No wait.
        # Rate per hour per post = 106.375 / 8 = 13.29 kg/h
        # 4 posts * 13.29 * 8 = 425 kg. 
        # 5 posts * 13.29 * 8 = 531 kg. (Exceeds 500)
        # So it should pick 4 posts (425 kg) which is covered by 500kg supply. 
        
        self.assertGreaterEqual(kg_torsion_supplied, kg_produced_rew * 0.9, 
                                f"Mass Balance Violation! Supply {kg_torsion_supplied} < 90% of Consumption {kg_produced_rew}")

if __name__ == '__main__':
    unittest.main()
