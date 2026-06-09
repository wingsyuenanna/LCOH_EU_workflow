## Battery Storage Diagram
```
┌────────────────────────────────────────────────────────────────────┐
│                     BATTERY STORAGE SYSTEM                         │
└────────────────────────────────────────────────────────────────────┘

                    POWER FLOWS (Decision Variables)
                    ═══════════════════════════════

   Solar DC                                            AC Bus
   ☀️ ──→                                              (serves load)
         │
         ↓
    ┌────────────────────┐
    │  SOLAR + INVERTER  │
    │                    │
    │  solar_used[h] ✓   │  ← Decision variable (optimizer chooses)
    │     (MW DC)        │
    └─────────┬──────────┘
              │
              │ After inverter: solar_used[h] × 0.967 (AC)
              │
         ┌────┴────┐
         │         │
         ↓         ↓
    charge[h] ✓    To Load
     (MW DC)       (AC)
    Decision       
    variable       
         │
         ↓
    ┌─────────────────────────────────────┐
    │       BATTERY STORAGE               │
    │                                     │
    │   ┌───────────────────────────┐    │
    │   │                           │    │
    │   │   soc[h] ⚠️               │    │  ← Dependent variable
    │   │   (MWh stored)            │    │     (calculated from charge/discharge)
    │   │                           │    │
    │   │   Current level: 250 MWh  │    │
    │   │   Capacity: B_MWh ✓       │    │  ← Decision variable
    │   │                           │    │
    │   └───────────────────────────┘    │
    │                                     │
    │   Power rating: B_MW ✓              │  ← Decision variable
    │                                     │
    └──────────────┬──────────────────────┘
                   │
                   ↓
              discharge[h] ✓   ← Decision variable (optimizer chooses)
                (MW DC)
                   │
                   ↓
         ┌─────────────────┐
         │ BATTERY INVERTER│
         │   (DC → AC)     │
         │   96.7% eff     │
         └────────┬─────────┘
                  │
                  ↓ discharge[h] × 0.967 (AC)
                  │
                  ↓
              To Load (AC)


         BINARY MODE SELECTOR (Decision Variable)
         ════════════════════════════════════════
         
              bess_mode[h] ✓
                    │
         ┌──────────┴──────────┐
         │                     │
    = 1 (Charge)          = 0 (Discharge)
         │                     │
         ↓                     ↓
   charge[h] allowed     discharge[h] allowed
   discharge[h] = 0      charge[h] = 0


         STATE TRACKING (Dependent Variable)
         ═══════════════════════════════════
         
    Hour h-1:                Hour h:
    soc[h-1] = 200 MWh      soc[h] = ? ⚠️
         │                       ↑
         │    charge[h] = 50 MW  │
         │    ↓ (adds ~49 MWh)   │
         └──────────────────────→│ soc[h] = 200 + 49 - 0 = 249 MWh
              discharge[h] = 0   │
              ↓ (removes 0 MWh)  │
                                 
    Constraint: soc[h] = soc[h-1] + charge[h]×η - discharge[h]/η
```