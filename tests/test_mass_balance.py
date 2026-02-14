import sys
import os

# Add the project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from integrations.openai_ia import assign_shift_greedy
import math

def test_mass_balance_enforcement():
    """
    Test that Rewinder consumption does not exceed Torsion supply significantly.
    Scenario:
    - Denier: 6000
    - Torsion Capacity for 6000: 500 kg/shift (mocked)
    - Rewinder Consumption per post: 106.37 kg/shift (mocked)
    - N Optimo: 3 posts
    - Valid posts: [3, 4, 5, 6, 7, 8, ...]
    
    Previous behavior:
    - Might assign 8 posts (851 kg consumption) regardless of 500 kg supply.
    
    Fixed behavior:
    - Should limit to a maximum of 5 posts (531.85 kg) because 500/531.85 = 94% (> 90% threshold).
    - 6 posts would be 638kg, 500/638 = 78% (< 90% threshold).
    """
    
    # Mock data
    backlog = [
        {
            'ref': 'REF-TEST',
            'descripcion': 'Test Reference',
            'denier': '6000',
            'kg_pendientes': 2000,
            'kg_total_inicial': 2000,
            'rw_rate': 13.296, # kg/h -> ~106.37 kg per 8h shift
            'n_optimo': 3,
            'valid_posts': [3, 4, 5, 6, 7, 8]
        }
    ]
    
    # Mock Torsion capacity: 1 machine of 62.5 kg/h -> 500 kg per 8h shift
    torsion_capacities = {
        '6000': {
            'total_kgh': 62.5,
            'machines': [
                {'machine_id': 'T-6000', 'kgh': 62.5, 'husos': 16}
            ]
        }
    }
    
    shift_duration = 8
    
    # Run assignment
    rw_assigns, tor_assigns = assign_shift_greedy(
        backlog,
        28,
        torsion_capacities,
        shift_duration
    )
    
    print("\n--- TEST RESULTS ---")
    if rw_assigns:
        a = rw_assigns[0]
        consumption = a['puestos'] * 13.296 * 8
        supply = sum(t['kg_turno'] for t in tor_assigns)
        
        print(f"Ref: {a['referencia']}")
        print(f"Posts Assigned: {a['puestos']}")
        print(f"Rewinder Consumption: {consumption:.2f} kg")
        print(f"Torsion Supply: {supply:.2f} kg")
        
        # Assertion: Supply must be at least 90% of consumption
        assert supply >= (consumption * 0.9), f"Imbalance: Supply {supply} < 90% of Consumption {consumption}"
        
        # Specific check for this scenario: should be exactly 5 posts
        # (Since 6 posts = 638kg vs 500kg supply is ~78%)
        assert a['puestos'] <= 5, f"Should not assign more than 5 posts, assigned {a['puestos']}"
        print("✅ Mass Balance Test Passed!")
    else:
        print("❌ No assignments made.")
        assert False

if __name__ == "__main__":
    try:
        test_mass_balance_enforcement()
    except AssertionError as e:
        print(f"❌ Test Failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected Error: {e}")
        sys.exit(1)
